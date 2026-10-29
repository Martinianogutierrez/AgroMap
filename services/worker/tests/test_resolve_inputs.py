from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pipeline.errors import PipelineError
from tests.synthetic import write_synthetic_field
from worker.resolve_inputs import build_pipeline_inputs, list_field_datasets


def test_build_inputs_from_local_datasets(tmp_path: Path):
    synth = write_synthetic_field(tmp_path / "f", n_seasons=2, seed=1)
    boundary_path = synth["workdir"] / "boundary.geojson"
    seasons = []
    for s in synth["inputs"]["seasons"]:
        seasons.append(
            {
                "id": f"ds-{s['year']}",
                "kind": "yield",
                "season_year": s["year"],
                "storage_path": f"owner/{s['yield_path']}",
                "meta": {"yield_column": "VRYIELDVOL"},
                "_local_path": str(synth["workdir"] / s["yield_path"]),
            }
        )
    datasets = [
        {
            "id": "b1",
            "kind": "boundary",
            "season_year": None,
            "storage_path": "owner/boundary.geojson",
            "meta": {},
            "_local_path": str(boundary_path),
        },
        *seasons,
    ]
    field = {
        "id": "11111111-1111-1111-1111-111111111111",
        "boundary": None,
        "crop_default": "soybean",
    }
    cfg = synth["config"]
    inputs = build_pipeline_inputs(
        field=field,
        datasets=datasets,
        config=cfg,
        workdir=tmp_path / "work",
        download_fn=None,
    )
    assert Path(inputs["boundary"]).exists()
    assert len(inputs["seasons"]) == 2
    assert all(Path(s["yield_path"]).exists() for s in inputs["seasons"])


def test_missing_boundary_raises(tmp_path: Path):
    field = {"id": "x", "boundary": None, "crop_default": None}
    with pytest.raises(PipelineError, match="boundary"):
        build_pipeline_inputs(
            field=field,
            datasets=[],
            config={"field_id": "11111111-1111-1111-1111-111111111111"},
            workdir=tmp_path,
            download_fn=None,
        )


def test_list_field_datasets_query_shape():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchall.return_value = [{"id": "1", "kind": "yield"}]
    rows = list_field_datasets(conn, "11111111-1111-1111-1111-111111111111")
    assert rows[0]["kind"] == "yield"
    assert "from public.datasets" in cur.execute.call_args[0][0].lower()
