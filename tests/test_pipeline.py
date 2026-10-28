from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Point, box

from src.overlay_engine import compute_productive_score, normalize_minmax
from src.pipeline import run_pipeline
from src.ingestion import clean_harvest_points


def _write_nested_harvest_zip(root: Path) -> Path:
    source_dir = root / "harvest_source"
    source_dir.mkdir()
    points = []
    yields = []
    for row in range(5):
        for column in range(5):
            points.append(Point(500_005 + column * 20, 6_100_005 + row * 20))
            yields.append(4.0 + column * 0.2 + row * 0.1)
    harvest = gpd.GeoDataFrame(
        {
            "VRYIELDMAS": yields,
            "VEHICLSPEE": [5.0] * len(points),
            "SWATHWIDTH": [10.0] * len(points),
        },
        geometry=points,
        crs="EPSG:32721",
    )
    shp_path = source_dir / "cebada.shp"
    harvest.to_file(shp_path, driver="ESRI Shapefile")
    inner_zip = root / "Cebada.zip"
    with zipfile.ZipFile(inner_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for component in source_dir.glob("cebada.*"):
            archive.write(component, arcname=component.name)
    outer_zip = root / "porvenir.zip"
    with zipfile.ZipFile(outer_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(inner_zip, arcname="Cebada.zip")
    return outer_zip


def _write_raster(path: Path, values: np.ndarray) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:32721",
        transform=from_origin(500_000, 6_100_100, 10, 10),
        nodata=-9999,
    ) as dataset:
        dataset.write(values.astype("float32"), 1)


def test_normalization_and_missing_layer_weight_renormalization() -> None:
    normalized = normalize_minmax(np.array([2.0, 4.0, np.nan]))
    np.testing.assert_allclose(normalized[:2], [0.0, 1.0])
    assert np.isnan(normalized[2])

    score = compute_productive_score(
        satellite=np.array([0.0, 2.0, np.nan]),
        drone=None,
        yield_layer=np.array([1.0, 0.0, 0.2]),
        weights={"satellite": 0.5, "drone": 0.0, "yield": 0.5},
    )
    np.testing.assert_allclose(score, [0.5, 0.5, 0.2])


def test_overlay_rejects_weights_that_do_not_sum_to_one() -> None:
    try:
        compute_productive_score(
            None,
            None,
            np.array([1.0, 2.0]),
            {"satellite": 0.2, "drone": 0.2, "yield": 0.2},
        )
    except ValueError as error:
        assert "sum to 1.0" in str(error)
    else:
        raise AssertionError("Invalid layer weights should be rejected")


def test_harvest_cleaning_filters_speed_swath_and_yield_anomalies() -> None:
    points = gpd.GeoDataFrame(
        {
            "VRYIELDMAS": [4.0, 4.5, 5.0, 13.0],
            "VEHICLSPEED": [5.0, 1.0, 5.0, 5.0],
            "SWATHWIDTH": [10.0, 10.0, 0.0, 10.0],
        },
        geometry=[
            Point(500_010, 6_100_010),
            Point(500_020, 6_100_020),
            Point(500_030, 6_100_030),
            Point(500_040, 6_100_040),
        ],
        crs="EPSG:32721",
    )

    cleaned = clean_harvest_points(points)

    assert len(cleaned) == 1
    assert cleaned.iloc[0]["VRYIELDMAS"] == 4.0


def test_nested_porvenir_pipeline_exports_machinery_shapefile(tmp_path: Path) -> None:
    harvest_zip = _write_nested_harvest_zip(tmp_path)
    boundary_path = tmp_path / "lote_6.shp"
    boundary = gpd.GeoDataFrame(
        {"name": ["synthetic field"]},
        geometry=[box(500_000, 6_100_000, 500_100, 6_100_100)],
        crs="EPSG:32721",
    )
    boundary.to_file(boundary_path)
    satellite_path = tmp_path / "satellite.tif"
    drone_path = tmp_path / "drone.tif"
    base = np.arange(100, dtype=float).reshape(10, 10)
    _write_raster(satellite_path, base)
    _write_raster(drone_path, base[::-1])

    zones, archive_path = run_pipeline(
        harvest_path=harvest_zip,
        boundary_path=boundary_path,
        output_dir=tmp_path / "outputs",
        harvest_match="cebada",
        satellite_path=satellite_path,
        drone_path=drone_path,
        weights={"satellite": 0.3, "drone": 0.3, "yield": 0.4},
        n_zones=3,
    )

    assert archive_path.is_file()
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert {"vra_prescription.shp", "vra_prescription.shx", "vra_prescription.dbf", "vra_prescription.prj"} <= names
    assert set(zones["ZONE_ID"]) == {1, 2, 3}
    assert set(zones["ZONE_NAME"]) == {"Low Potential", "Medium Potential", "High Potential"}
    assert set(zones["SEED_RATE"]) == {55_000, 70_000, 85_000}
    assert zones["AVG_YIELD"].between(0.5, 12.0).all()

    exported = gpd.read_file(archive_path.parent / "vra_prescription.shp")
    assert {"ZONE_ID", "ZONE_NAME", "SEED_RATE", "AVG_YIELD"}.issubset(exported.columns)
    assert exported.crs.to_epsg() == 32721
