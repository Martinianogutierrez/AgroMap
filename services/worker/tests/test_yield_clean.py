import geopandas as gpd
import numpy as np
from shapely.geometry import Point, box

from pipeline.yield_clean import clean_yield_gdf, suggest_column_mapping


def test_suggest_mapping():
    m = suggest_column_mapping(["Latitude", "Longitude", "Yld_Mass_Dry"])
    assert m["lat"] is not None
    assert m["lon"] is not None


def test_clean_removes_zeros_and_outliers():
    rng = np.random.default_rng(0)
    n = 100
    xs = rng.uniform(0, 100, n)
    ys = rng.uniform(0, 100, n)
    yld = rng.normal(3000, 100, n)
    yld[0] = 0
    yld[1] = 50000
    gdf = gpd.GeoDataFrame(
        {"VRYIELDVOL": yld, "geometry": [Point(x, y) for x, y in zip(xs, ys, strict=False)]},
        crs="EPSG:32721",
    )
    boundary = gpd.GeoDataFrame(geometry=[box(0, 0, 100, 100)], crs="EPSG:32721")
    out, rep = clean_yield_gdf(
        gdf, column="VRYIELDVOL", boundary_utm=boundary, outlier_sigma=3
    )
    assert rep.n_out < rep.n_in
    assert (out["_yield_kg_ha"] > 0).all()
