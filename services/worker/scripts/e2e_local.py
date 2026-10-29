#!/usr/bin/env python3
"""
Offline e2e: synthetic field -> pipeline subprocess -> validate shapefile.
No Supabase required. Used by `make e2e` when DATABASE_URL unset.
"""

from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.synthetic import write_synthetic_field  # noqa: E402
from worker.subprocess_run import run_pipeline_subprocess  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="fpz-e2e-") as td:
        td_path = Path(td)
        synth = write_synthetic_field(td_path / "field", n_seasons=3, seed=11)
        cfg = json.loads(synth["config"].model_dump_json(by_alias=True))
        inputs = {
            "boundary": str(synth["workdir"] / "boundary.geojson"),
            "seasons": [
                {
                    "year": s["year"],
                    "yield_path": str(synth["workdir"] / s["yield_path"]),
                }
                for s in synth["inputs"]["seasons"]
            ],
        }
        result = run_pipeline_subprocess(
            config=cfg,
            inputs=inputs,
            workdir=td_path / "run",
            timeout_sec=300,
        )
        assert result["ok"], result
        shp = result.get("shp_zip")
        assert shp and Path(shp).exists(), "missing shapefile zip"
        with zipfile.ZipFile(shp) as zf:
            assert any(n.endswith(".shp") for n in zf.namelist())
            zf.extractall(td_path / "shp")
        gdf = gpd.read_file(td_path / "shp" / "zones.shp")
        for c in gdf.columns:
            if c != "geometry":
                assert len(c) <= 10, c
        assert len(gdf) >= 1
        print("e2e_local OK", {"n_zones": len(gdf), "shp": shp})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
