from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
from PIL import Image

from pipeline.ingest import write_grid_geotiff

ReportFn = Callable[[str, int, str], None]

SHP_FIELDS = [
    ("ZONE_ID", "zone_id"),
    ("ZONE_NAME", "zone_name"),
    ("AREA_HA", "area_ha"),
    ("YLD_MEAN", "yld_mean"),
    ("YLD_CV", "yld_cv"),
    ("NDVI_MEAN", "ndvi_mean"),
    ("N_SEASONS", "n_seasons"),
    ("GEN_DATE", "gen_date"),
]


def _prepare_shp_gdf(zones: gpd.GeoDataFrame, n_seasons: int) -> gpd.GeoDataFrame:
    gdf = zones.copy()
    gdf["n_seasons"] = n_seasons
    gdf["gen_date"] = date.today().isoformat()
    out = gpd.GeoDataFrame(geometry=gdf.geometry, crs=gdf.crs)
    for shp_name, src in SHP_FIELDS:
        out[shp_name] = gdf[src] if src in gdf.columns else None
    # enforce <=10
    for c in list(out.columns):
        if c != "geometry" and len(c) > 10:
            raise ValueError(f"shapefile field too long: {c}")
    return out


def export_shapefile_zip(zones: gpd.GeoDataFrame, n_seasons: int, out_zip: Path) -> Path:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_zip.parent / "_shp_tmp"
    tmp_dir.mkdir(exist_ok=True)
    gdf = _prepare_shp_gdf(zones, n_seasons)
    shp_path = tmp_dir / "zones.shp"
    gdf.to_file(shp_path)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in tmp_dir.glob("zones.*"):
            zf.write(p, arcname=p.name)
    for p in tmp_dir.glob("zones.*"):
        p.unlink()
    tmp_dir.rmdir()
    return out_zip


def export_gpkg(zones: gpd.GeoDataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    zones.to_file(path, driver="GPKG")
    return path


def export_zones_tif(
    labels: np.ndarray, transform, crs, path: Path
) -> Path:
    data = labels.astype("float32")
    data[data < 0] = np.nan
    return write_grid_geotiff(data, transform, crs, path)


def export_preview_png(labels: np.ndarray, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lab = labels.copy()
    lab[lab < 0] = -1
    mx = max(int(lab.max()), 0)
    # simple palette
    palette = np.array(
        [
            [0, 0, 0],
            [165, 0, 38],
            [215, 48, 39],
            [254, 224, 144],
            [26, 152, 80],
            [0, 104, 55],
        ],
        dtype=np.uint8,
    )
    rgb = np.zeros((*lab.shape, 3), dtype=np.uint8)
    for i in range(0, mx + 1):
        rgb[lab == i] = palette[(i + 1) % len(palette)]
    Image.fromarray(rgb, mode="RGB").save(path)
    return path


def write_report(path: Path, report: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def write_params(path: Path, params: dict[str, Any]) -> Path:
    return write_report(path, params)
