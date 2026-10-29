from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Point, box

from pipeline.errors import PipelineError
from worker.ingest_handlers import inspect_dataset, set_field_boundary_from_file


def test_inspect_yield_gpkg(tmp_path: Path):
    gdf = gpd.GeoDataFrame(
        {
            "Yld_Vol_Dr": [1.0, 2.0, 3.0],
            "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)],
        },
        crs="EPSG:4326",
    )
    path = tmp_path / "y.gpkg"
    gdf.to_file(path, driver="GPKG")
    meta = inspect_dataset(path, kind="yield")
    assert meta["feature_count"] == 3
    assert meta["crs"] is not None
    assert meta["suggested_mapping"]["yield"] is not None
    assert "bbox" in meta


def test_inspect_corrupt_file(tmp_path: Path):
    bad = tmp_path / "bad.tif"
    bad.write_bytes(b"not-a-tiff")
    with pytest.raises(PipelineError, match="(?i)open|read|invalid|corrupt|raster"):
        inspect_dataset(bad, kind="orthomosaic")


def test_inspect_raster_bands(tmp_path: Path):
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "ms.tif"
    data = np.random.randint(0, 255, (4, 16, 16), dtype=np.uint8)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=16,
        width=16,
        count=4,
        dtype="uint8",
        crs="EPSG:32721",
        transform=from_origin(500000, 6300000, 10, 10),
    ) as dst:
        dst.write(data)
    meta = inspect_dataset(path, kind="orthomosaic")
    assert meta["bands"] == 4
    assert meta["crs"]


def test_set_boundary(tmp_path: Path):
    gdf = gpd.GeoDataFrame(geometry=[box(-60, -33, -59.9, -32.9)], crs="EPSG:4326")
    path = tmp_path / "b.geojson"
    gdf.to_file(path, driver="GeoJSON")
    gj = set_field_boundary_from_file(path)
    assert gj["type"] in ("Polygon", "MultiPolygon")
