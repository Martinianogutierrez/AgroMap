from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import geopandas as gpd
import numpy as np
from pyproj import CRS
from rasterio.transform import from_origin
from scipy.spatial import cKDTree

from pipeline.errors import PipelineError
from pipeline.ingest import write_grid_geotiff
from pipeline.qgis_runtime import qgis_available

ReportFn = Callable[[str, int, str], None]


def _grid_for_boundary(
    boundary_utm: gpd.GeoDataFrame, resolution_m: float
) -> tuple[np.ndarray, np.ndarray, object, int, int]:
    minx, miny, maxx, maxy = boundary_utm.total_bounds
    width = max(1, int(np.ceil((maxx - minx) / resolution_m)))
    height = max(1, int(np.ceil((maxy - miny) / resolution_m)))
    transform = from_origin(minx, maxy, resolution_m, resolution_m)
    xs = minx + (np.arange(width) + 0.5) * resolution_m
    ys = maxy - (np.arange(height) + 0.5) * resolution_m
    xx, yy = np.meshgrid(xs, ys)
    return xx, yy, transform, height, width


def idw_scipy(
    points: gpd.GeoDataFrame,
    value_col: str,
    boundary_utm: gpd.GeoDataFrame,
    resolution_m: float,
    power: float = 2.0,
    k: int = 12,
) -> tuple[np.ndarray, object, CRS]:
    if len(points) == 0:
        raise PipelineError("IDW requires at least one point")
    xx, yy, transform, height, width = _grid_for_boundary(boundary_utm, resolution_m)
    coords = np.column_stack([points.geometry.x.values, points.geometry.y.values])
    values = points[value_col].astype(float).values
    tree = cKDTree(coords)
    grid_pts = np.column_stack([xx.ravel(), yy.ravel()])
    dists, idxs = tree.query(grid_pts, k=min(k, len(points)))
    if dists.ndim == 1:
        dists = dists[:, None]
        idxs = idxs[:, None]
    dists = np.maximum(dists, 1e-6)
    w = 1.0 / np.power(dists, power)
    w_sum = w.sum(axis=1)
    interp = (w * values[idxs]).sum(axis=1) / w_sum
    arr = interp.reshape(height, width)

    from shapely import contains_xy

    poly = boundary_utm.geometry.union_all()
    mask = contains_xy(poly, xx, yy)
    arr = np.where(mask, arr, np.nan)
    crs = CRS.from_user_input(points.crs) if points.crs else CRS.from_epsg(4326)
    return arr.astype("float32"), transform, crs


def interpolate_idw(
    points: gpd.GeoDataFrame,
    value_col: str,
    boundary_utm: gpd.GeoDataFrame,
    resolution_m: float,
    out_tif: Path,
    report: ReportFn | None = None,
    prefer_qgis: bool = True,
) -> Path:
    used = "scipy"
    if prefer_qgis and qgis_available():
        try:
            # QGIS path reserved for container; fall through on any error
            raise RuntimeError("use scipy path in default build; QGIS wired in Docker hardening")
        except Exception:
            used = "scipy_fallback"

    arr, transform, crs = idw_scipy(points, value_col, boundary_utm, resolution_m)
    write_grid_geotiff(arr, transform, crs, out_tif)
    if report:
        report("interpolate", 50, f"IDW via {used} → {out_tif.name}")
    return out_tif
