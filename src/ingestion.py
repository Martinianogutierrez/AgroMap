"""Spatial input loading, validation, cleaning, and 10 m grid preparation."""

from __future__ import annotations

import math
import shutil
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from scipy.spatial import cKDTree
from shapely.geometry import box

DEFAULT_CRS = "EPSG:32721"
DEFAULT_RESOLUTION_M = 10.0
YIELD_FIELD = "VRYIELDMAS"
SPEED_FIELD = "VEHICLSPEED"
SWATH_FIELD = "SWATHWIDTH"


def read_vector(path: str | Path, layer: str | None = None) -> gpd.GeoDataFrame:
    """Read a vector dataset and reject inputs without a defined CRS."""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Vector input does not exist: {source}")
    frame = gpd.read_file(source, layer=layer)
    if frame.empty:
        raise ValueError(f"Vector input contains no features: {source}")
    if frame.crs is None:
        raise ValueError(f"Vector input has no CRS: {source}")
    if frame.geometry.isna().any() or frame.geometry.is_empty.any():
        frame = frame.loc[frame.geometry.notna() & ~frame.geometry.is_empty].copy()
    if frame.empty:
        raise ValueError(f"Vector input contains no usable geometries: {source}")
    return frame


def extract_nested_archives(
    source: str | Path, destination: str | Path, max_depth: int = 4
) -> list[Path]:
    """Safely extract a ZIP and ZIPs nested inside it, returning extracted files."""
    archive_path = Path(source)
    target_root = Path(destination).resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(f"Archive does not exist: {archive_path}")
    if not zipfile.is_zipfile(archive_path):
        raise ValueError(f"Input is not a ZIP archive: {archive_path}")
    target_root.mkdir(parents=True, exist_ok=True)

    extracted: list[Path] = []
    pending: list[tuple[Path, int]] = [(archive_path, 0)]
    while pending:
        current_archive, depth = pending.pop()
        if depth > max_depth:
            raise ValueError(f"Nested ZIP depth exceeds the limit of {max_depth}")
        archive_folder = target_root / f"archive_{depth}_{len(extracted)}"
        archive_folder.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(current_archive) as zipped:
            for member in zipped.infolist():
                member_path = Path(member.filename)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ValueError(f"Unsafe path in ZIP archive: {member.filename}")
                destination_path = (archive_folder / member_path).resolve()
                if not destination_path.is_relative_to(target_root):
                    raise ValueError(f"Unsafe path in ZIP archive: {member.filename}")
                if member.is_dir():
                    destination_path.mkdir(parents=True, exist_ok=True)
                    continue
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(member) as source_file, destination_path.open("wb") as target_file:
                    shutil.copyfileobj(source_file, target_file)
                extracted.append(destination_path)
                if destination_path.suffix.lower() == ".zip":
                    pending.append((destination_path, depth + 1))
    return extracted


def discover_harvest_shapefile(
    source: str | Path,
    workspace: str | Path,
    name_match: str | None = None,
    yield_field: str = YIELD_FIELD,
) -> Path:
    """Find the unique harvest shapefile in a directory or nested ZIP archive."""
    source_path = Path(source)
    if not source_path.exists():
        raise FileNotFoundError(f"Harvest input does not exist: {source_path}")
    if source_path.is_dir():
        candidates = list(source_path.rglob("*.shp"))
    elif source_path.suffix.lower() == ".shp":
        candidates = [source_path]
    elif source_path.suffix.lower() == ".zip":
        extract_nested_archives(source_path, workspace)
        candidates = list(Path(workspace).rglob("*.shp"))
    else:
        raise ValueError("Harvest input must be a .shp, .zip, or directory")

    if name_match:
        candidates = [
            candidate
            for candidate in candidates
            if name_match.casefold() in candidate.as_posix().casefold()
        ]

    matches: list[Path] = []
    for candidate in candidates:
        try:
            columns = gpd.read_file(candidate, rows=0).columns
        except (OSError, ValueError, zipfile.BadZipFile):
            continue
        if yield_field.casefold() in {str(column).casefold() for column in columns}:
            matches.append(candidate)

    if len(matches) == 1:
        return matches[0]
    if not matches:
        qualifier = f" matching {name_match!r}" if name_match else ""
        raise ValueError(
            f"No shapefile{qualifier} with a {yield_field} field was found in {source_path}"
        )
    options = "\n".join(f"  - {candidate}" for candidate in matches)
    raise ValueError(
        "More than one harvest shapefile matches; choose the crop/campaign with "
        f"--harvest-match:\n{options}"
    )


def _column_name(frame: gpd.GeoDataFrame, requested: str) -> str:
    matches = [column for column in frame.columns if str(column).casefold() == requested.casefold()]
    if not matches:
        dbf_name = requested[:10].casefold()
        matches = [column for column in frame.columns if str(column).casefold() == dbf_name]
    if not matches:
        raise ValueError(f"Harvest data is missing required field {requested!r}")
    return str(matches[0])


def clean_harvest_points(
    points: gpd.GeoDataFrame,
    yield_field: str = YIELD_FIELD,
    speed_field: str = SPEED_FIELD,
    swath_field: str = SWATH_FIELD,
    target_crs: str = DEFAULT_CRS,
) -> gpd.GeoDataFrame:
    """Filter invalid harvest records, deduplicate coincident points, and project them."""
    if points.crs is None:
        raise ValueError("Harvest points have no CRS")
    if points.empty:
        raise ValueError("Harvest points contain no features")
    yield_column = _column_name(points, yield_field)
    speed_column = _column_name(points, speed_field)
    swath_column = _column_name(points, swath_field)

    cleaned = points.copy()
    for column in (yield_column, speed_column, swath_column):
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    yield_values = cleaned[yield_column].to_numpy(dtype=float)
    speed_values = cleaned[speed_column].to_numpy(dtype=float)
    swath_values = cleaned[swath_column].to_numpy(dtype=float)
    valid_geometry = (
        cleaned.geometry.notna()
        & ~cleaned.geometry.is_empty
        & cleaned.geometry.geom_type.eq("Point")
    )
    valid_values = (
        np.isfinite(yield_values)
        & np.isfinite(speed_values)
        & np.isfinite(swath_values)
        & (speed_values > 1.0)
        & (swath_values > 0.0)
        & (yield_values >= 0.5)
        & (yield_values <= 12.0)
    )
    cleaned = cleaned.loc[valid_geometry & valid_values].copy()
    if cleaned.empty:
        raise ValueError("No harvest points remain after speed, swath, and yield filtering")

    cleaned = cleaned.to_crs(target_crs)
    cleaned = cleaned.loc[~cleaned.geometry.duplicated(keep="first")].copy()
    if cleaned.empty:
        raise ValueError("No unique harvest points remain after removing duplicate coordinates")
    return cleaned


def create_grid(
    boundary: gpd.GeoDataFrame,
    resolution_m: float = DEFAULT_RESOLUTION_M,
    target_crs: str = DEFAULT_CRS,
) -> gpd.GeoDataFrame:
    """Create 10 m cell polygons clipped to the supplied field boundary."""
    if resolution_m <= 0 or not math.isfinite(resolution_m):
        raise ValueError("Grid resolution must be a finite positive number")
    if boundary.crs is None:
        raise ValueError("Field boundary has no CRS")
    projected = boundary.to_crs(target_crs)
    polygons = projected.geometry
    if not polygons.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError("Field boundary must contain only Polygon or MultiPolygon features")
    field_geometry = polygons.union_all()
    if field_geometry.is_empty or field_geometry.area <= 0:
        raise ValueError("Field boundary has no usable area")
    if not field_geometry.is_valid:
        field_geometry = field_geometry.buffer(0)
    if field_geometry.is_empty or field_geometry.area <= 0:
        raise ValueError("Field boundary could not be repaired into a valid area")

    min_x, min_y, max_x, max_y = field_geometry.bounds
    origin_x = math.floor(min_x / resolution_m) * resolution_m
    origin_y = math.floor(min_y / resolution_m) * resolution_m
    columns = math.ceil((max_x - origin_x) / resolution_m)
    rows = math.ceil((max_y - origin_y) / resolution_m)
    records: list[dict[str, object]] = []
    for row in range(rows):
        y0 = origin_y + row * resolution_m
        for column in range(columns):
            x0 = origin_x + column * resolution_m
            cell = box(x0, y0, x0 + resolution_m, y0 + resolution_m)
            if not cell.intersects(field_geometry):
                continue
            clipped = cell.intersection(field_geometry)
            if clipped.is_empty or clipped.area <= 0:
                continue
            records.append({"row": row, "col": column, "geometry": clipped})
    if not records:
        raise ValueError("No grid cells intersect the field boundary")
    return gpd.GeoDataFrame(records, geometry="geometry", crs=target_crs)


def interpolate_yield_idw(
    points: gpd.GeoDataFrame,
    grid: gpd.GeoDataFrame,
    yield_field: str = YIELD_FIELD,
    neighbors: int = 12,
    power: float = 2.0,
    max_distance_m: float = 100.0,
) -> np.ndarray:
    """Interpolate harvest yield at grid centroids using inverse-distance weighting."""
    if points.crs is None or grid.crs is None:
        raise ValueError("Harvest points and grid must both have defined CRSs")
    if (
        neighbors < 1
        or not math.isfinite(power)
        or power <= 0
        or not math.isfinite(max_distance_m)
        or max_distance_m <= 0
    ):
        raise ValueError("IDW neighbors, power, and maximum distance must be positive")
    column = _column_name(points, yield_field)
    point_coords = np.column_stack((points.geometry.x, points.geometry.y))
    values = points[column].to_numpy(dtype=float)
    query_points = grid.geometry.centroid
    query_coords = np.column_stack((query_points.x, query_points.y))
    neighbor_count = min(neighbors, len(points))
    tree = cKDTree(point_coords)
    distances, indices = tree.query(
        query_coords, k=neighbor_count, distance_upper_bound=max_distance_m
    )
    if neighbor_count == 1:
        distances = distances[:, np.newaxis]
        indices = indices[:, np.newaxis]
    result = np.full(len(grid), np.nan, dtype=float)
    for index, (cell_distances, cell_indices) in enumerate(zip(distances, indices)):
        valid = np.isfinite(cell_distances) & (cell_indices < len(values))
        if not valid.any():
            continue
        valid_distances = cell_distances[valid]
        valid_values = values[cell_indices[valid]]
        exact = valid_distances <= 1e-8
        if exact.any():
            result[index] = float(valid_values[exact][0])
            continue
        weights = 1.0 / np.power(valid_distances, power)
        result[index] = float(np.average(valid_values, weights=weights))
    return result


def sample_raster_to_grid(
    raster_path: str | Path,
    grid: gpd.GeoDataFrame,
    band: int | None = None,
) -> np.ndarray:
    """Sample a raster at cell centroids, transforming coordinates to raster CRS."""
    source = Path(raster_path)
    if not source.is_file():
        raise FileNotFoundError(f"Raster input does not exist: {source}")
    if grid.crs is None:
        raise ValueError("Grid has no CRS")
    with rasterio.open(source) as dataset:
        if dataset.crs is None:
            raise ValueError(f"Raster has no CRS: {source}")
        if band is None:
            if dataset.count != 1:
                raise ValueError(
                    f"{source} has {dataset.count} bands; select an index band explicitly"
                )
            band = 1
        if band < 1 or band > dataset.count:
            raise ValueError(f"Raster band must be between 1 and {dataset.count}")
        centroids = grid.geometry.centroid
        transformer = Transformer.from_crs(grid.crs, dataset.crs, always_xy=True)
        xs, ys = transformer.transform(centroids.x.to_numpy(), centroids.y.to_numpy())
        coordinates = list(zip(xs, ys))
        pixel_rows, pixel_columns = rasterio.transform.rowcol(dataset.transform, xs, ys)
        block_height, block_width = dataset.block_shapes[band - 1]
        order = np.lexsort(
            (
                np.asarray(pixel_columns) // block_width,
                np.asarray(pixel_rows) // block_height,
            )
        )
        result = np.full(len(grid), np.nan, dtype=float)
        for index in order:
            sample = next(dataset.sample([coordinates[index]], indexes=band, masked=True))
            if np.ma.is_masked(sample[0]):
                continue
            value = float(sample[0])
            if math.isfinite(value) and (dataset.nodata is None or value != dataset.nodata):
                result[index] = value
    if not np.isfinite(result).any():
        raise ValueError(f"Raster has no valid pixels over the field grid: {source}")
    return result
