from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
import numpy as np
import pandas as pd
import yaml

from pipeline.crs_util import hemisphere_from_lat
from pipeline.errors import PipelineError

ReportFn = Callable[[str, int, str], None]
MetricName = Literal[
    "precip_sum_mm", "gdd_base10", "tmax_mean", "heat_days_gt35", "dry_spell_max"
]


@dataclass
class Decision:
    included: bool
    value: float
    reference: float
    reason: str


def load_crop_calendars(path: Path | None = None) -> dict:
    p = path or Path(__file__).with_name("crop_calendars.yaml")
    with p.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def season_window(
    year: int,
    crop: str | None,
    lat: float,
    window_cfg: dict | None = None,
    calendars: dict | None = None,
) -> tuple[date, date]:
    if window_cfg and window_cfg.get("mode") == "fixed_md":
        start_md = window_cfg.get("start_md") or "01-01"
        end_md = window_cfg.get("end_md") or "12-31"
    else:
        cals = calendars or load_crop_calendars()
        key = (crop or "generic").lower()
        crop_cal = cals.get(key) or cals["generic"]
        hemi = hemisphere_from_lat(lat)
        slot = crop_cal.get(hemi) or crop_cal.get("south") or crop_cal.get("north")
        start_md = (window_cfg or {}).get("start_md") or slot["start_md"]
        end_md = (window_cfg or {}).get("end_md") or slot["end_md"]

    sm, sd = map(int, start_md.split("-"))
    em, ed = map(int, end_md.split("-"))
    start = date(year, sm, sd)
    # window crossing new year → end in year+1 (southern summer crops)
    if (em, ed) < (sm, sd):
        end = date(year + 1, em, ed)
    else:
        end = date(year, em, ed)
    return start, end


def metric_value(df: pd.DataFrame, metric: MetricName) -> float:
    if df.empty:
        return float("nan")
    if metric == "precip_sum_mm":
        return float(df["precipitation_sum"].sum())
    if metric == "gdd_base10":
        tmean = (df["temperature_2m_max"] + df["temperature_2m_min"]) / 2.0
        return float(np.maximum(tmean - 10.0, 0).sum())
    if metric == "tmax_mean":
        return float(df["temperature_2m_max"].mean())
    if metric == "heat_days_gt35":
        return float((df["temperature_2m_max"] > 35).sum())
    if metric == "dry_spell_max":
        dry = (df["precipitation_sum"].fillna(0) < 1.0).astype(int).values
        best = cur = 0
        for v in dry:
            cur = cur + 1 if v else 0
            best = max(best, cur)
        return float(best)
    raise PipelineError(f"unknown metric {metric}")


def apply_rule(
    values: dict[int, float],
    rule: dict,
) -> dict[int, Decision]:
    op = rule.get("op", "above_mean")
    tol = float(rule.get("tolerance_pct") or 0) / 100.0
    finite = {y: v for y, v in values.items() if v == v}
    if not finite:
        return {
            y: Decision(False, v, float("nan"), "no metric value")
            for y, v in values.items()
        }
    ref = float(np.mean(list(finite.values())))
    out: dict[int, Decision] = {}
    if op in ("above_mean", "below_mean"):
        for y, v in values.items():
            if v != v:
                out[y] = Decision(False, v, ref, "nan metric")
                continue
            if op == "above_mean":
                ok = v >= ref * (1.0 - tol)
                reason = f"{'above' if ok else 'below'} mean {ref:.2f} (tol {tol:.0%})"
            else:
                ok = v <= ref * (1.0 + tol)
                reason = f"{'below' if ok else 'above'} mean {ref:.2f} (tol {tol:.0%})"
            out[y] = Decision(ok, float(v), ref, reason)
        return out
    if op == "between":
        lo, hi = float(rule["lo"]), float(rule["hi"])
        for y, v in values.items():
            ok = v == v and lo <= v <= hi
            out[y] = Decision(ok, float(v) if v == v else v, ref, f"between {lo}..{hi}")
        return out
    if op in ("top_n", "bottom_n"):
        n = int(rule.get("n") or 1)
        ordered = sorted(finite.items(), key=lambda kv: kv[1], reverse=(op == "top_n"))
        chosen = {y for y, _ in ordered[:n]}
        for y, v in values.items():
            out[y] = Decision(y in chosen, float(v) if v == v else v, ref, f"{op} n={n}")
        return out
    raise PipelineError(f"unknown rule op {op}")


def _cache_path(cache_dir: Path, lat: float, lon: float, start: date, end: date) -> Path:
    key = f"{lat:.4f}_{lon:.4f}_{start.isoformat()}_{end.isoformat()}.json"
    return cache_dir / key


def fetch_daily(
    lat: float,
    lon: float,
    start: date,
    end: date,
    *,
    cache_dir: Path | None = None,
    client: httpx.Client | None = None,
    fixture: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if fixture is not None:
        return fixture.copy()
    cache_dir = cache_dir or Path(".cache/open-meteo")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cp = _cache_path(cache_dir, lat, lon, start, end)
    if cp.exists():
        data = json.loads(cp.read_text(encoding="utf-8"))
        return pd.DataFrame(data)

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min",
        "timezone": "UTC",
    }
    own = client is None
    client = client or httpx.Client(timeout=60.0)
    try:
        r = client.get("https://archive-api.open-meteo.com/v1/archive", params=params)
        r.raise_for_status()
        payload = r.json()["daily"]
    finally:
        if own:
            client.close()
    df = pd.DataFrame(payload)
    df["time"] = pd.to_datetime(df["time"]).dt.date
    cp.write_text(df.to_json(orient="records", default_handler=str), encoding="utf-8")
    return df


def filter_seasons(
    years: list[int],
    *,
    lat: float,
    lon: float,
    crop: str | None,
    weather_filter: dict | None,
    cache_dir: Path | None = None,
    fixtures: dict[int, pd.DataFrame] | None = None,
    report: ReportFn | None = None,
) -> tuple[list[int], dict[int, dict[str, Any]]]:
    if not weather_filter or not weather_filter.get("enabled"):
        table = {
            y: asdict(Decision(True, float("nan"), float("nan"), "filter disabled"))
            for y in years
        }
        return list(years), table

    metric: MetricName = weather_filter.get("metric") or "precip_sum_mm"
    window_cfg = weather_filter.get("window") or {}
    values: dict[int, float] = {}
    for y in years:
        start, end = season_window(y, crop, lat, window_cfg)
        fix = fixtures.get(y) if fixtures else None
        df = fetch_daily(lat, lon, start, end, cache_dir=cache_dir, fixture=fix)
        # trim to window if fixture is wider
        if "time" in df.columns:
            df = df[(df["time"] >= start) & (df["time"] <= end)]
        values[y] = metric_value(df, metric)

    decisions = apply_rule(values, weather_filter.get("rule") or {"op": "above_mean"})
    included = [y for y, d in decisions.items() if d.included]
    min_seasons = int(weather_filter.get("min_seasons") or 1)
    table = {y: asdict(d) for y, d in decisions.items()}
    if report:
        report("seasons", 20, f"weather kept {len(included)}/{len(years)} seasons")
    if len(included) < min_seasons:
        raise PipelineError(
            f"only {len(included)} seasons passed weather filter (min_seasons={min_seasons})",
            details={"decisions": table},
        )
    return included, table


def synthetic_daily(
    start: date, end: date, precip_scale: float = 1.0, seed: int = 0
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = (end - start).days + 1
    rows = []
    for i in range(days):
        d = start + timedelta(days=i)
        rows.append(
            {
                "time": d,
                "precipitation_sum": float(max(0, rng.normal(3.0 * precip_scale, 2.0))),
                "temperature_2m_max": float(rng.normal(28, 4)),
                "temperature_2m_min": float(rng.normal(16, 3)),
            }
        )
    return pd.DataFrame(rows)
