import json
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest

from pipeline.run import run
from tests.synthetic import write_synthetic_field


@pytest.fixture()
def synth(tmp_path: Path):
    return write_synthetic_field(tmp_path / "field", n_seasons=3, seed=7)


def test_e2e_offline(synth):
    cfg = synth["config"]
    inputs = {
        "boundary": synth["inputs"]["boundary"]
        if not isinstance(synth["inputs"]["boundary"], str)
        else str(synth["workdir"] / "boundary.geojson"),
        "seasons": [
            {
                "year": s["year"],
                "yield_path": str(synth["workdir"] / s["yield_path"]),
            }
            for s in synth["inputs"]["seasons"]
        ],
        "weather_fixtures": synth["inputs"]["weather_fixtures"],
    }
    # ensure boundary path
    inputs["boundary"] = str(synth["workdir"] / "boundary.geojson")
    out_dir = synth["workdir"] / "out"
    outs = run(cfg, inputs, out_dir)

    assert outs.report_json and outs.report_json.exists()
    report = json.loads(outs.report_json.read_text(encoding="utf-8"))
    assert "seasons" in report
    assert outs.shp_zip and outs.shp_zip.exists()
    assert outs.gpkg and outs.gpkg.exists()
    assert outs.preview_png and outs.preview_png.exists()

    with zipfile.ZipFile(outs.shp_zip) as zf:
        names = zf.namelist()
        assert any(n.endswith(".shp") for n in names)
        assert any(n.endswith(".prj") for n in names)

    gdf = gpd.read_file(outs.gpkg)
    for col in gdf.columns:
        if col != "geometry":
            # exported gpkg may keep long names; shapefile path enforces 10
            pass
    assert gdf["zone_id"].nunique() >= 1
    assert gdf["area_ha"].min() >= cfg.zones.min_area_ha * 0.5  # merge tolerance

    # shapefile field length via re-read from zip extract
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(outs.shp_zip) as zf:
            zf.extractall(td)
        shp = gpd.read_file(Path(td) / "zones.shp")
        for c in shp.columns:
            if c != "geometry":
                assert len(c) <= 10, c
