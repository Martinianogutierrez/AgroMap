from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union

from pipeline.errors import PipelineError
from pipeline.yield_clean import suggest_column_mapping

MAX_FEATURES = 2_000_000
MAX_RASTER_PIXELS = 500_000_000


def _as_multipolygon_geojson(gdf: gpd.GeoDataFrame) -> dict:
    if gdf.crs is None:
        raise PipelineError(
            "boundary has no CRS; set CRS (EPSG) on the dataset form and re-upload"
        )
    gdf = gdf.to_crs(4326)
    geom = unary_union(gdf.geometry)
    if geom.is_empty:
        raise PipelineError("boundary geometry is empty")
    if isinstance(geom, Polygon):
        geom = MultiPolygon([geom])
    return mapping(geom)


def set_field_boundary_from_file(path: Path) -> dict:
    path = Path(path)
    try:
        if path.suffix.lower() == ".zip":
            gdf = gpd.read_file(f"zip://{path}")
        else:
            gdf = gpd.read_file(path)
    except Exception as e:
        raise PipelineError(f"could not read boundary file: {e}") from e
    return _as_multipolygon_geojson(gdf)


def inspect_dataset(path: Path, kind: str) -> dict[str, Any]:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        raise PipelineError("uploaded file is empty or missing")

    # zip-slip guard for zips
    if path.suffix.lower() == ".zip" or path.name.lower().endswith(".shp.zip"):
        _assert_zip_safe(path)

    if kind in ("orthomosaic", "satellite") or path.suffix.lower() in {
        ".tif",
        ".tiff",
        ".vrt",
    }:
        return _inspect_raster(path)

    return _inspect_vector(path, kind=kind)


def _assert_zip_safe(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    raise PipelineError("zip contains unsafe paths (zip-slip)")
                if info.file_size > 500_000_000:
                    raise PipelineError("zip member too large")
    except zipfile.BadZipFile as e:
        raise PipelineError("corrupt or invalid zip file") from e


def _inspect_raster(path: Path) -> dict[str, Any]:
    try:
        import rasterio
    except ImportError as e:
        raise PipelineError("rasterio required to inspect rasters") from e
    try:
        with rasterio.open(path) as src:
            if src.width * src.height * src.count > MAX_RASTER_PIXELS:
                raise PipelineError(
                    f"raster too large ({src.width}x{src.height}x{src.count}); downsample or clip"
                )
            crs = str(src.crs) if src.crs else None
            if crs is None:
                raise PipelineError(
                    "raster has no CRS; set EPSG on the dataset form and re-upload"
                )
            bounds = src.bounds
            return {
                "type": "raster",
                "crs": crs,
                "bands": src.count,
                "width": src.width,
                "height": src.height,
                "dtype": str(src.dtypes[0]),
                "bbox": [bounds.left, bounds.bottom, bounds.right, bounds.top],
                "suggested_mapping": {},
            }
    except PipelineError:
        raise
    except Exception as e:
        raise PipelineError(f"could not open raster (corrupt or unsupported): {e}") from e


def _inspect_vector(path: Path, kind: str) -> dict[str, Any]:
    try:
        if path.suffix.lower() == ".csv":
            return _inspect_csv(path)
        if path.suffix.lower() == ".zip" or path.name.lower().endswith(".shp.zip"):
            gdf = gpd.read_file(f"zip://{path}")
        else:
            gdf = gpd.read_file(path)
    except PipelineError:
        raise
    except Exception as e:
        raise PipelineError(f"could not open vector file: {e}") from e

    if len(gdf) > MAX_FEATURES:
        raise PipelineError(f"too many features ({len(gdf)}); max {MAX_FEATURES}")

    crs = str(gdf.crs) if gdf.crs else None
    if crs is None and kind != "yield":
        raise PipelineError(
            "file has no CRS; set EPSG on the dataset form and re-upload"
        )
    # yield points without CRS: still flag
    cols = [str(c) for c in gdf.columns if c != "geometry"]
    suggested = suggest_column_mapping(cols)
    bbox = None
    if gdf.crs is not None and not gdf.empty:
        try:
            b = gdf.to_crs(4326).total_bounds
            bbox = [float(x) for x in b]
        except Exception:
            bbox = [float(x) for x in gdf.total_bounds]

    meta: dict[str, Any] = {
        "type": "vector",
        "crs": crs,
        "feature_count": int(len(gdf)),
        "columns": cols,
        "suggested_mapping": suggested,
        "bbox": bbox,
    }
    if crs is None:
        meta["crs_missing"] = True
        meta["action"] = (
            "Set CRS (EPSG code) on the dataset form and re-run ingest; "
            "cannot georeference yield points without CRS."
        )
    return meta


def _inspect_csv(path: Path) -> dict[str, Any]:
    import pandas as pd

    try:
        df = pd.read_csv(path, nrows=5)
    except Exception as e:
        raise PipelineError(f"could not read CSV: {e}") from e
    cols = [str(c) for c in df.columns]
    suggested = suggest_column_mapping(cols)
    # full count
    try:
        n = sum(1 for _ in open(path, encoding="utf-8", errors="ignore")) - 1
    except Exception:
        n = len(df)
    if n > MAX_FEATURES:
        raise PipelineError(f"CSV too many rows ({n})")
    meta = {
        "type": "csv",
        "crs": None,
        "crs_missing": True,
        "feature_count": int(max(n, 0)),
        "columns": cols,
        "suggested_mapping": suggested,
        "bbox": None,
        "action": (
            "CSV yield needs lat/lon columns (or join to geometry) and CRS assumption WGS84; "
            "confirm suggested_mapping on the dataset form."
        ),
    }
    return meta


def geojson_geometry_only(obj: dict) -> dict:
    if obj.get("type") == "Feature":
        return obj["geometry"]
    if obj.get("type") == "FeatureCollection":
        geoms = [shape(f["geometry"]) for f in obj["features"]]
        u = unary_union(geoms)
        if isinstance(u, Polygon):
            u = MultiPolygon([u])
        return mapping(u)
    return obj
