from __future__ import annotations

from collections.abc import Callable

import geopandas as gpd
import numpy as np
from rasterio import features
from rasterio.transform import Affine
from scipy import ndimage
from shapely.geometry import shape
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from pipeline.errors import PipelineError

ReportFn = Callable[[str, int, str], None]

ZONE_NAMES = {
    2: ["Low", "High"],
    3: ["Low", "Medium", "High"],
    4: ["Low", "Med-Low", "Med-High", "High"],
    5: ["VeryLow", "Low", "Medium", "High", "VeryHigh"],
}


def choose_k(x: np.ndarray, k_min: int, k_max: int, seed: int = 42) -> int:
    best_k, best_score = k_min, -1.0
    for k in range(k_min, k_max + 1):
        if k >= len(x):
            break
        km = KMeans(n_clusters=k, n_init=10, random_state=seed)
        labels = km.fit_predict(x)
        if len(set(labels)) < 2:
            continue
        score = float(silhouette_score(x, labels))
        if score > best_score:
            best_k, best_score = k, score
    return best_k


def cluster_layers(
    feature_arrays: list[np.ndarray],
    valid_mask: np.ndarray,
    *,
    k: int | str,
    k_min: int,
    k_max: int,
    seed: int = 42,
    yield_mean: np.ndarray | None = None,
) -> np.ndarray:
    if not feature_arrays:
        raise PipelineError("no feature layers for clustering")
    stacked = np.stack(feature_arrays, axis=-1)
    ys, xs = np.where(valid_mask)
    if len(ys) < max(k_min, 3):
        raise PipelineError("too few valid pixels to cluster")
    samples = stacked[ys, xs]
    ok = np.isfinite(samples).all(axis=1)
    ys, xs, samples = ys[ok], xs[ok], samples[ok]
    scaler = StandardScaler()
    x = scaler.fit_transform(samples)
    kk = choose_k(x, k_min, k_max, seed=seed) if k == "auto" else int(k)
    km = KMeans(n_clusters=kk, n_init=10, random_state=seed)
    labels = km.fit_predict(x)
    order_src = yield_mean if yield_mean is not None else feature_arrays[0]
    means = [
        float(np.nanmean(order_src[ys[labels == c], xs[labels == c]])) for c in range(kk)
    ]
    order = np.argsort(means)
    remap = {int(old): int(new) for new, old in enumerate(order)}
    labels = np.array([remap[int(v)] for v in labels], dtype=np.int32)
    out = np.full(valid_mask.shape, -1, dtype=np.int32)
    out[ys, xs] = labels
    return out


def majority_filter(labels: np.ndarray, size: int = 3) -> np.ndarray:
    if size <= 1:
        return labels

    def mode_block(values: np.ndarray) -> float:
        vals = values[values >= 0]
        if vals.size == 0:
            return -1.0
        bc = np.bincount(vals.astype(int))
        return float(bc.argmax())

    return ndimage.generic_filter(
        labels.astype(float), mode_block, size=size, mode="nearest"
    ).astype(np.int32)


def _merge_small(gdf: gpd.GeoDataFrame, min_area_ha: float) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    for _ in range(20):
        gdf["area_ha"] = gdf.geometry.area / 10000.0
        small_idx = gdf.index[gdf["area_ha"] < min_area_ha].tolist()
        large = gdf.loc[gdf["area_ha"] >= min_area_ha]
        if not small_idx or large.empty:
            break
        for idx in small_idx:
            if idx not in gdf.index:
                continue
            geom = gdf.loc[idx].geometry
            dists = large.geometry.distance(geom)
            target = dists.idxmin()
            gdf.loc[idx, "zone_id"] = int(gdf.loc[target, "zone_id"])
        gdf = gdf.dissolve(by="zone_id", as_index=False)
    return gdf


def polygonize_zones(
    labels: np.ndarray,
    transform: Affine,
    crs,
    min_area_ha: float,
) -> gpd.GeoDataFrame:
    mask = labels >= 0
    shapes_iter = features.shapes(labels.astype(np.int32), mask=mask, transform=transform)
    rows = []
    for geom, val in shapes_iter:
        if int(val) < 0:
            continue
        g = shape(geom)
        if not g.is_empty:
            rows.append({"zone_id": int(val), "geometry": g})
    if not rows:
        raise PipelineError("polygonize produced no zones")
    gdf = gpd.GeoDataFrame(rows, crs=crs).dissolve(by="zone_id", as_index=False)
    gdf = _merge_small(gdf, min_area_ha)
    gdf = gdf.dissolve(by="zone_id", as_index=False)
    gdf["area_ha"] = gdf.geometry.area / 10000.0
    k = int(gdf["zone_id"].max()) + 1 if len(gdf) else 0
    names = ZONE_NAMES.get(k) or [f"Z{i + 1}" for i in range(k)]
    gdf["zone_name"] = gdf["zone_id"].map(
        lambda i: names[i] if i < len(names) else f"Z{i + 1}"
    )
    return gdf


def zone_stats(
    gdf: gpd.GeoDataFrame,
    yld_mean: np.ndarray | None,
    yld_cv: np.ndarray | None,
    ndvi_mean: np.ndarray | None,
    transform: Affine,
) -> gpd.GeoDataFrame:
    from rasterio.features import geometry_mask

    out = gdf.copy()
    ref = yld_mean if yld_mean is not None else ndvi_mean
    if ref is None:
        out["yld_mean"] = np.nan
        out["yld_cv"] = np.nan
        out["ndvi_mean"] = np.nan
        return out
    h, w = ref.shape
    for col, arr in (("yld_mean", yld_mean), ("yld_cv", yld_cv), ("ndvi_mean", ndvi_mean)):
        vals = []
        for geom in out.geometry:
            if arr is None:
                vals.append(np.nan)
                continue
            m = geometry_mask([geom], out_shape=(h, w), transform=transform, invert=True)
            vals.append(float(np.nanmean(arr[m])) if m.any() else np.nan)
        out[col] = vals
    return out
