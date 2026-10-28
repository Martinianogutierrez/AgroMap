"""Management-zone classification, spatial cleanup, and rate assignment."""

from __future__ import annotations

from collections.abc import Mapping

import geopandas as gpd
import numpy as np
from scipy.ndimage import generic_filter
from sklearn.cluster import KMeans


_DEFAULT_RATES = (55_000, 70_000, 85_000)
_ZONE_NAMES = {
    3: ("Low Potential", "Medium Potential", "High Potential"),
    4: ("Low Potential", "Medium-Low", "Medium-High", "High Potential"),
    5: ("Very Low", "Low", "Medium", "High", "Very High"),
}


def _majority_filter(values: np.ndarray) -> float:
    valid = values[values >= 0]
    if valid.size == 0:
        return -1.0
    labels, counts = np.unique(valid.astype(int), return_counts=True)
    return float(labels[counts == counts.max()].min())


def smooth_grid_labels(
    grid: gpd.GeoDataFrame, labels: np.ndarray, size: int = 3
) -> np.ndarray:
    """Apply a majority filter to a regular grid while preserving missing cells."""
    if size < 1 or size % 2 == 0:
        raise ValueError("Smoothing window size must be a positive odd integer")
    if not {"row", "col"}.issubset(grid.columns):
        raise ValueError("Grid must include integer 'row' and 'col' coordinates")
    if len(labels) != len(grid):
        raise ValueError("There must be one label for each grid cell")
    rows = grid["row"].to_numpy(dtype=int)
    columns = grid["col"].to_numpy(dtype=int)
    label_values = np.asarray(labels, dtype=int)
    raster = np.full((rows.max() + 1, columns.max() + 1), -1, dtype=int)
    raster[rows, columns] = label_values
    smoothed = generic_filter(
        raster.astype(float), _majority_filter, size=size, mode="constant", cval=-1
    ).astype(int)
    smoothed[raster < 0] = -1
    return smoothed[rows, columns]


def classify_zones(
    grid: gpd.GeoDataFrame,
    score_column: str = "score",
    yield_column: str = "yield",
    n_zones: int = 3,
    seed_rates: Mapping[int, int] | None = None,
    smoothing_size: int = 3,
) -> gpd.GeoDataFrame:
    """Cluster score values, smooth cell labels, and dissolve polygons by zone."""
    if n_zones < 3 or n_zones > 5:
        raise ValueError("Management zones must be between 3 and 5")
    if grid.empty:
        raise ValueError("Cannot classify an empty grid")
    if score_column not in grid:
        raise ValueError(f"Grid is missing score column {score_column!r}")
    if yield_column not in grid:
        raise ValueError(f"Grid is missing yield column {yield_column!r}")
    scores = grid[score_column].to_numpy(dtype=float)
    valid = np.isfinite(scores)
    if not valid.all():
        raise ValueError(f"Grid contains {int((~valid).sum())} cells without a score")
    unique_count = np.unique(scores).size
    cluster_count = min(n_zones, unique_count)
    if cluster_count < 3:
        raise ValueError("At least three distinct productive scores are required")

    model = KMeans(n_clusters=cluster_count, random_state=42, n_init=10)
    raw_labels = model.fit_predict(scores.reshape(-1, 1))
    ordered_clusters = np.argsort(model.cluster_centers_.ravel())
    rank_for_cluster = {cluster: rank for rank, cluster in enumerate(ordered_clusters)}
    ordered_labels = np.array([rank_for_cluster[label] for label in raw_labels], dtype=int)
    ordered_labels = smooth_grid_labels(grid, ordered_labels, size=smoothing_size)
    present_zones = sorted(np.unique(ordered_labels[ordered_labels >= 0]).tolist())
    if len(present_zones) < 3:
        raise ValueError(
            "Spatial smoothing collapsed the map below three zones; "
            "use a smaller smoothing window or review the score distribution"
        )

    zone_names = _ZONE_NAMES.get(len(present_zones))
    if zone_names is None:
        zone_names = tuple(f"Zone {rank + 1}" for rank in range(len(present_zones)))
    if seed_rates is None:
        if len(present_zones) == 3:
            rates = list(_DEFAULT_RATES)
        else:
            rates = np.rint(np.linspace(55_000, 85_000, len(present_zones))).astype(int).tolist()
    else:
        missing_rates = [zone_id for zone_id in range(1, len(present_zones) + 1) if zone_id not in seed_rates]
        if missing_rates:
            raise ValueError(f"Missing target seeding rates for zone IDs: {missing_rates}")
        rates = [int(seed_rates[zone_id]) for zone_id in range(1, len(present_zones) + 1)]
        if any(rate <= 0 for rate in rates):
            raise ValueError("Target seeding rates must be positive integers")

    records = grid.copy()
    records["_zone_rank"] = ordered_labels
    records = records.loc[records["_zone_rank"] >= 0].copy()
    records["ZONE_ID"] = records["_zone_rank"].map(
        {rank: zone_id for zone_id, rank in enumerate(present_zones, start=1)}
    ).astype("int32")
    name_for_zone = {index + 1: name for index, name in enumerate(zone_names)}
    rate_for_zone = {index + 1: rate for index, rate in enumerate(rates)}
    records["ZONE_NAME"] = records["ZONE_ID"].map(name_for_zone)
    records["SEED_RATE"] = records["ZONE_ID"].map(rate_for_zone).astype("int32")
    records["cell_area"] = records.geometry.area
    records["yield_weighted"] = records[yield_column].astype(float) * records["cell_area"]
    grouped = records.dissolve(
        by=["ZONE_ID", "ZONE_NAME", "SEED_RATE"],
        aggfunc={"cell_area": "sum", "yield_weighted": "sum"},
        as_index=False,
    )
    grouped["AVG_YIELD"] = np.where(
        grouped["cell_area"] > 0,
        grouped["yield_weighted"] / grouped["cell_area"],
        np.nan,
    )
    grouped = grouped.drop(columns=["cell_area", "yield_weighted"])
    grouped["ZONE_ID"] = grouped["ZONE_ID"].astype("int32")
    grouped["SEED_RATE"] = grouped["SEED_RATE"].astype("int32")
    grouped["AVG_YIELD"] = grouped["AVG_YIELD"].astype(float)
    return grouped[["ZONE_ID", "ZONE_NAME", "SEED_RATE", "AVG_YIELD", "geometry"]]
