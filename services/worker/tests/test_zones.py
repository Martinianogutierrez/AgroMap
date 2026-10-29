import numpy as np
from rasterio.transform import from_origin

from pipeline.zones import choose_k, cluster_layers, majority_filter, polygonize_zones


def test_cluster_and_polygonize():
    h, w = 40, 40
    xx, yy = np.meshgrid(np.arange(w), np.arange(h))
    layer = (xx + yy).astype("float32")
    mask = np.ones((h, w), dtype=bool)
    labels = cluster_layers(
        [layer],
        mask,
        k=3,
        k_min=3,
        k_max=5,
        seed=0,
        yield_mean=layer,
    )
    assert set(np.unique(labels)) <= {-1, 0, 1, 2}
    labels = majority_filter(labels, size=3)
    transform = from_origin(0, 40, 1, 1)
    gdf = polygonize_zones(labels, transform, "EPSG:32721", min_area_ha=0.0001)
    assert len(gdf) >= 1
    assert gdf.geometry.area.sum() > 0


def test_choose_k():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 1, (50, 2))
    b = rng.normal(5, 1, (50, 2))
    x = np.vstack([a, b])
    k = choose_k(x, 2, 4, seed=0)
    assert 2 <= k <= 4
