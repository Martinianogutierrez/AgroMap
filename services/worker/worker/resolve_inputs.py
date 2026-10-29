from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pipeline.config import AnalysisConfig, load_config
from pipeline.errors import PipelineError
from worker import db

DownloadFn = Callable[[str, str, Path], Path]


def list_field_datasets(conn, field_id: str) -> list[dict[str, Any]]:
    return db.list_datasets_for_field(conn, field_id)


def _write_boundary_geojson(boundary: Any, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(boundary, str):
        try:
            boundary = json.loads(boundary)
        except json.JSONDecodeError:
            dest.write_text(boundary, encoding="utf-8")
            return dest
    if isinstance(boundary, dict):
        if boundary.get("type") in ("FeatureCollection", "Feature"):
            dest.write_text(json.dumps(boundary), encoding="utf-8")
        else:
            feat = {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "properties": {}, "geometry": boundary}
                ],
            }
            dest.write_text(json.dumps(feat), encoding="utf-8")
        return dest
    raise PipelineError("unsupported boundary value on field")


def build_pipeline_inputs(
    *,
    field: dict[str, Any],
    datasets: list[dict[str, Any]],
    config: AnalysisConfig | dict,
    workdir: Path | str,
    download_fn: DownloadFn | None,
    bucket: str = "uploads",
) -> dict[str, Any]:
    """Resolve field boundary + yield seasons to local paths for pipeline.run."""
    cfg = load_config(config) if not isinstance(config, AnalysisConfig) else config
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    inputs_dir = workdir / "inputs"
    inputs_dir.mkdir(exist_ok=True)

    boundary_path = inputs_dir / "boundary.geojson"
    boundary_src = field.get("boundary")
    boundary_ds = next((d for d in datasets if d.get("kind") == "boundary"), None)

    if boundary_src:
        _write_boundary_geojson(boundary_src, boundary_path)
    elif boundary_ds:
        local = boundary_ds.get("_local_path")
        if local:
            shutil.copy(str(local), boundary_path)
        elif download_fn:
            download_fn(bucket, boundary_ds["storage_path"], boundary_path)
        else:
            raise PipelineError("boundary dataset present but no download path")
    else:
        raise PipelineError(
            "field has no boundary; upload a boundary GeoJSON or draw one before analysis"
        )

    yield_rows = [d for d in datasets if d.get("kind") == "yield"]
    if cfg.layers.use_yield and not yield_rows:
        raise PipelineError("no yield datasets on this field; upload yield maps first")

    seasons: list[dict[str, Any]] = []
    for d in yield_rows:
        year = d.get("season_year")
        if year is None:
            raise PipelineError(
                f"yield dataset {d.get('original_name') or d.get('id')} missing season_year"
            )
        oname = d.get("original_name") or d.get("storage_path") or "yield.gpkg"
        suffix = Path(str(oname)).suffix or ".gpkg"
        dest = inputs_dir / f"yield_{year}_{str(d['id'])[:8]}{suffix}"

        local = d.get("_local_path")
        if local:
            shutil.copy(str(local), dest)
        elif download_fn:
            download_fn(bucket, d["storage_path"], dest)
        else:
            raise PipelineError(f"cannot resolve yield dataset {d.get('id')}")

        seasons.append(
            {
                "year": int(year),
                "yield_path": str(dest),
                "dataset_id": str(d["id"]),
                "meta": d.get("meta") or {},
            }
        )

    weather_fixtures = None
    for d in datasets:
        meta = d.get("meta") or {}
        if meta.get("weather_fixtures_path"):
            import pickle

            with open(meta["weather_fixtures_path"], "rb") as f:
                weather_fixtures = pickle.load(f)

    return {
        "boundary": str(boundary_path),
        "seasons": seasons,
        "weather_fixtures": weather_fixtures,
    }
