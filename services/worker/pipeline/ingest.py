from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
from pyproj import CRS
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from pipeline.crs_util import utm_crs_from_geom
from pipeline.errors import PipelineError

ReportFn = Callable[[str, int, str], None]


def _noop_report(step: str, pct: int, msg: str) -> None:
    return None


def detect_kind(path: Path) -> str:
    suf = path.suffix.lower()
    name = path.name.lower()
    if suf in {".tif", ".tiff", ".vrt"}:
        return "raster"
    if suf in {".shp", ".gpkg", ".geojson", ".json", ".csv"}:
        return "vector"
    if name.endswith(".shp.zip") or suf == ".zip":
        return "vector"
    raise PipelineError(f"unsupported input type: {path.name}")


def _as_multipolygon(geom: BaseGeometry) -> BaseGeometry:
    u = unary_union(geom)
    if isinstance(u, Polygon):
        return MultiPolygon([u])
    return u


def load_boundary(path: Path | dict | BaseGeometry | str) -> gpd.GeoDataFrame:
    if isinstance(path, BaseGeometry):
        gdf = gpd.GeoDataFrame(geometry=[_as_multipolygon(path)], crs="EPSG:4326")
        return gdf
    if isinstance(path, dict):
        if path.get("type") == "FeatureCollection":
            geoms = [shape(f["geometry"]) for f in path["features"]]
            geom = _as_multipolygon(unary_union(geoms))
        elif path.get("type") == "Feature":
            geom = _as_multipolygon(shape(path["geometry"]))
        else:
            geom = _as_multipolygon(shape(path))
        return gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")

    p = Path(path)
    if p.suffix.lower() == ".csv":
        raise PipelineError("boundary cannot be CSV")
    gdf = gpd.read_file(p)
    if gdf.crs is None:
        raise PipelineError(
            "boundary has no CRS; set CRS in the dataset form (EPSG code) and re-upload"
        )
    gdf = gdf.to_crs(4326)
    geom = _as_multipolygon(unary_union(gdf.geometry))
    if geom.is_empty:
        raise PipelineError("boundary geometry is empty")
    return gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")


def reproject_clip_vector(
    src: Path,
    boundary_wgs84: gpd.GeoDataFrame,
    out_path: Path,
    report: ReportFn = _noop_report,
) -> gpd.GeoDataFrame:
    report("ingest", 10, f"read vector {src.name}")
    gdf = gpd.read_file(src)
    if gdf.crs is None:
        raise PipelineError(f"{src.name} has no CRS; set it on the dataset and re-run")
    utm = utm_crs_from_geom(boundary_wgs84.geometry.iloc[0])
    boundary_utm = boundary_wgs84.to_crs(utm)
    gdf = gdf.to_crs(utm)
    clipped = gpd.clip(gdf, boundary_utm)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    clipped.to_file(out_path, driver="GPKG")
    report("ingest", 30, f"wrote {out_path.name}")
    return clipped


def write_grid_geotiff(
    array: np.ndarray,
    transform: Any,
    crs: CRS | Any,
    path: Path,
    nodata: float = np.nan,
) -> Path:
    import rasterio
    from rasterio.enums import Resampling

    path.parent.mkdir(parents=True, exist_ok=True)
    count = 1 if array.ndim == 2 else array.shape[0]
    height, width = array.shape[-2], array.shape[-1]
    data = array if array.ndim == 3 else array[np.newaxis, ...]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=count,
        dtype=str(data.dtype),
        crs=crs,
        transform=transform,
        nodata=nodata,
        compress="deflate",
        tiled=True,
    ) as dst:
        dst.write(data)
        dst.build_overviews([2, 4, 8], Resampling.average)
        dst.update_tags(ns="rio_overview", resampling="average")
    return path


def boundary_to_geojson_dict(boundary: gpd.GeoDataFrame) -> dict:
    return json.loads(boundary.to_crs(4326).to_json())
