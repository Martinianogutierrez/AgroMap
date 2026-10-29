from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import geopandas as gpd
import pandas as pd

from pipeline.errors import PipelineError

ReportFn = Callable[[str, int, str], None]

YIELD_ALIASES = (
    "vryieldvol",
    "yield",
    "yld",
    "dry_yield",
    "yld_mass_d",
    "mass_yield",
    "yieldmas",
    "yield_kg_ha",
)
LAT_ALIASES = ("lat", "latitude", "y", "gps_lat")
LON_ALIASES = ("lon", "lng", "long", "longitude", "x", "gps_lon")


def _norm(c: str) -> str:
    return "".join(ch for ch in c.lower() if ch.isalnum() or ch == "_")


def suggest_column_mapping(columns: list[str]) -> dict[str, str | None]:
    norms = {_norm(c): c for c in columns}
    def pick(aliases: tuple[str, ...]) -> str | None:
        for a in aliases:
            if a in norms:
                return norms[a]
            for n, orig in norms.items():
                if a in n:
                    return orig
        return None

    return {
        "yield": pick(YIELD_ALIASES),
        "lat": pick(LAT_ALIASES),
        "lon": pick(LON_ALIASES),
    }


@dataclass
class CleanReport:
    n_in: int = 0
    n_out: int = 0
    removed: dict[str, int] = field(default_factory=dict)


def _to_kg_ha(series: pd.Series, unit: str) -> pd.Series:
    if unit == "kg/ha":
        return series.astype(float)
    if unit == "t/ha":
        return series.astype(float) * 1000.0
    if unit == "bu/ac":
        # generic grain approx; document in DECISIONS
        return series.astype(float) * 67.25
    raise PipelineError(f"unknown yield unit: {unit}")


def clean_yield_gdf(
    gdf: gpd.GeoDataFrame,
    *,
    column: str,
    unit: str = "kg/ha",
    outlier_sigma: float = 3.0,
    remove_zeros: bool = True,
    boundary_utm: gpd.GeoDataFrame | None = None,
    report: ReportFn | None = None,
) -> tuple[gpd.GeoDataFrame, CleanReport]:
    rep = CleanReport(n_in=len(gdf))
    if column not in gdf.columns:
        suggested = suggest_column_mapping(list(map(str, gdf.columns)))
        raise PipelineError(
            f"yield column '{column}' not found",
            details={"suggested_mapping": suggested, "columns": list(map(str, gdf.columns))},
        )

    out = gdf.copy()
    out["_yield_kg_ha"] = _to_kg_ha(out[column], unit)

    def drop_mask(mask: pd.Series, reason: str) -> None:
        n = int(mask.sum())
        if n:
            rep.removed[reason] = rep.removed.get(reason, 0) + n
            nonlocal out
            out = out.loc[~mask].copy()

    drop_mask(out["_yield_kg_ha"].isna(), "null")
    if remove_zeros:
        drop_mask(out["_yield_kg_ha"] <= 0, "zero_or_neg")

    if boundary_utm is not None:
        poly = boundary_utm.geometry.union_all()
        inside = out.within(poly)
        drop_mask(~inside, "outside_boundary")

    if len(out) == 0:
        raise PipelineError("no yield points left after cleaning")

    vals = out["_yield_kg_ha"].astype(float)
    mu, sd = float(vals.mean()), float(vals.std(ddof=0) or 0.0)
    if sd > 0 and outlier_sigma > 0:
        drop_mask((vals - mu).abs() > outlier_sigma * sd, "sigma")

    if len(out) >= 4:
        q1, q3 = vals.quantile(0.25), vals.quantile(0.75)
        iqr = float(q3 - q1)
        if iqr > 0:
            lo, hi = float(q1 - 1.5 * iqr), float(q3 + 1.5 * iqr)
            drop_mask((out["_yield_kg_ha"] < lo) | (out["_yield_kg_ha"] > hi), "iqr")

    for col, reason in (("Speed", "speed"), ("Swath", "swath"), ("DISTANCE", "lag")):
        if col in out.columns:
            s = pd.to_numeric(out[col], errors="coerce")
            if s.notna().any():
                m, sd2 = float(s.mean()), float(s.std(ddof=0) or 0)
                if sd2 > 0:
                    drop_mask((s - m).abs() > 3 * sd2, reason)

    if len(out) == 0:
        raise PipelineError("no yield points left after outlier filters")

    rep.n_out = len(out)
    if report:
        report("yield_clean", 40, f"kept {rep.n_out}/{rep.n_in} points")
    return out, rep
