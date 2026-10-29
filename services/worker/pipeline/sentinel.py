"""Sentinel-2 L2A helpers. Network calls mocked in tests via fixtures."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import numpy as np

from pipeline.errors import PipelineError
from pipeline.ndvi import ndvi_from_nir_red

ReportFn = Callable[[str, int, str], None]

EARTH_SEARCH = "https://earth-search.aws.element84.com/v1"


def scl_clear_mask(scl: np.ndarray) -> np.ndarray:
    good = {4, 5, 6, 7}
    return np.isin(scl, list(good))


def median_ndvi_composite(scenes: list[dict[str, np.ndarray]]) -> np.ndarray:
    if not scenes:
        raise PipelineError("no Sentinel-2 scenes for NDVI composite")
    stack = []
    for s in scenes:
        nd = ndvi_from_nir_red(s["B08"], s["B04"])
        mask = scl_clear_mask(s["SCL"])
        nd = np.where(mask, nd, np.nan)
        stack.append(nd)
    return np.nanmedian(np.stack(stack, axis=0), axis=0).astype("float32")


def search_items_stub(
    bbox: list[float],
    start: date,
    end: date,
    *,
    fixture_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if fixture_items is not None:
        return fixture_items
    raise PipelineError(
        "live STAC search not enabled here; pass fixture_items or use networked worker"
    )


def windowed_ndvi_from_fixtures(
    fixture_scenes: list[dict[str, np.ndarray]],
    report: ReportFn | None = None,
) -> np.ndarray:
    out = median_ndvi_composite(fixture_scenes)
    if report:
        report("ndvi", 60, f"sentinel median composite n={len(fixture_scenes)}")
    return out
