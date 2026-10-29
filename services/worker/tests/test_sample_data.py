from pathlib import Path

from pipeline.yield_clean import suggest_column_mapping
from worker.ingest_handlers import inspect_dataset


def test_sample_scripts_and_mapping(tmp_path: Path):
    from scripts.make_sample_data import (
        write_john_deere_csv,
        write_nir_geotiff,
        write_yield_shp_zip,
    )

    csv = write_john_deere_csv(tmp_path / "jd.csv")
    meta = inspect_dataset(csv, kind="yield")
    assert meta["suggested_mapping"]["yield"] is not None
    assert meta["suggested_mapping"]["lat"] is not None

    z = write_yield_shp_zip(tmp_path / "y.zip")
    meta2 = inspect_dataset(z, kind="yield")
    assert meta2["feature_count"] > 0
    assert meta2["crs"]

    tif = write_nir_geotiff(tmp_path / "o.tif")
    meta3 = inspect_dataset(tif, kind="orthomosaic")
    assert meta3["bands"] == 4


def test_fuzzy_aliases():
    m = suggest_column_mapping(["GpsLatitude", "GpsLongitude", "Dry_Yield"])
    assert m["lat"]
    assert m["lon"]
    assert m["yield"]
