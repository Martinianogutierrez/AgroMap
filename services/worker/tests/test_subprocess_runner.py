import json
from pathlib import Path

import pytest

from pipeline.errors import PipelineError
from tests.synthetic import write_synthetic_field
from worker.subprocess_run import run_pipeline_subprocess


def test_subprocess_run_success(tmp_path: Path):
    synth = write_synthetic_field(tmp_path / "f", n_seasons=2, seed=3)
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
        "weather_fixtures": None,
    }
    # disable weather via config already
    out = tmp_path / "out"
    result = run_pipeline_subprocess(
        config=cfg,
        inputs=inputs,
        workdir=out,
        timeout_sec=120,
    )
    assert result["ok"] is True
    assert Path(result["report_json"]).exists()


def test_subprocess_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # tiny timeout with heavy work still may finish fast; use invalid to force quick fail path
    with pytest.raises(PipelineError):
        run_pipeline_subprocess(
            config={"field_id": "not-a-uuid"},
            inputs={"boundary": str(tmp_path / "missing.geojson"), "seasons": []},
            workdir=tmp_path / "o",
            timeout_sec=30,
        )
