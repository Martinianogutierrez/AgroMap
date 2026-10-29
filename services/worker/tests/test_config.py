import pytest
from pydantic import ValidationError

from pipeline.config import AnalysisConfig, load_config


def test_defaults():
    cfg = load_config({"field_id": "11111111-1111-1111-1111-111111111111"})
    assert cfg.zones.k == "auto"
    assert cfg.resolution_m == 10
    assert cfg.yield_.column == "VRYIELDVOL"


def test_extra_forbid():
    with pytest.raises(ValidationError):
        load_config({"field_id": "11111111-1111-1111-1111-111111111111", "nope": 1})


def test_k_min_max():
    with pytest.raises(ValidationError):
        AnalysisConfig(
            field_id="11111111-1111-1111-1111-111111111111",
            zones={"k_min": 6, "k_max": 3},
        )


def test_yield_alias():
    cfg = load_config(
        {
            "field_id": "11111111-1111-1111-1111-111111111111",
            "yield": {"column": "YLD", "unit": "t/ha"},
        }
    )
    assert cfg.yield_.column == "YLD"
    assert cfg.yield_.unit == "t/ha"
