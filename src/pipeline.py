"""End-to-end processing orchestration for a field boundary and its input layers."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import geopandas as gpd
import numpy as np

from src.clustering import classify_zones
from src.export import export_prescription
from src.ingestion import (
    DEFAULT_CRS,
    clean_harvest_points,
    create_grid,
    discover_harvest_shapefile,
    interpolate_yield_idw,
    read_vector,
    sample_raster_to_grid,
)
from src.overlay_engine import compute_productive_score, validate_weights


def run_pipeline(
    harvest_path: str | Path,
    boundary_path: str | Path,
    output_dir: str | Path,
    *,
    harvest_match: str | None = None,
    satellite_path: str | Path | None = None,
    drone_path: str | Path | None = None,
    satellite_band: int | None = None,
    drone_band: int | None = None,
    weights: dict[str, float] | None = None,
    resolution_m: float = 10.0,
    n_zones: int = 3,
    max_idw_distance_m: float = 100.0,
) -> tuple[gpd.GeoDataFrame, Path]:
    """Create a management-zone layer and a machinery-ready shapefile ZIP."""
    if n_zones < 3 or n_zones > 5:
        raise ValueError("Management zones must be between 3 and 5")
    if weights is None:
        available_count = 1 + int(satellite_path is not None) + int(drone_path is not None)
        weights = {
            "satellite": 1.0 / available_count if satellite_path is not None else 0.0,
            "drone": 1.0 / available_count if drone_path is not None else 0.0,
            "yield": 1.0 / available_count,
        }
    weights = validate_weights(weights)
    if weights["satellite"] > 0 and satellite_path is None:
        raise ValueError("Satellite weight is positive but no satellite raster was provided")
    if weights["drone"] > 0 and drone_path is None:
        raise ValueError("Drone weight is positive but no drone raster was provided")

    boundary = read_vector(boundary_path)
    with TemporaryDirectory(prefix="vra_harvest_") as temporary_dir:
        harvest_file = discover_harvest_shapefile(
            harvest_path, temporary_dir, name_match=harvest_match
        )
        points = clean_harvest_points(read_vector(harvest_file), target_crs=DEFAULT_CRS)

    projected_boundary = boundary.to_crs(DEFAULT_CRS)
    boundary_geometry = projected_boundary.geometry.union_all()
    points = points.loc[points.geometry.intersects(boundary_geometry)].copy()
    if points.empty:
        raise ValueError("No cleaned harvest points intersect the field boundary")

    grid = create_grid(projected_boundary, resolution_m=resolution_m, target_crs=DEFAULT_CRS)
    grid["yield"] = interpolate_yield_idw(
        points, grid, max_distance_m=max_idw_distance_m
    )
    satellite = (
        sample_raster_to_grid(satellite_path, grid, satellite_band)
        if satellite_path is not None and weights["satellite"] > 0
        else None
    )
    drone = (
        sample_raster_to_grid(drone_path, grid, drone_band)
        if drone_path is not None and weights["drone"] > 0
        else None
    )
    grid["satellite"] = satellite if satellite is not None else np.nan
    grid["drone"] = drone if drone is not None else np.nan
    grid["score"] = compute_productive_score(
        satellite, drone, grid["yield"].to_numpy(dtype=float), weights
    )
    missing_scores = int(grid["score"].isna().sum())
    if missing_scores:
        raise ValueError(
            f"{missing_scores} grid cells have no available input values; "
            "check raster coverage or increase the IDW distance"
        )
    zones = classify_zones(grid, n_zones=n_zones)
    archive = export_prescription(zones, output_dir)
    return zones, archive
