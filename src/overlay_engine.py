"""Layer normalization and weighted productive-potential scoring."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

LAYER_NAMES = ("satellite", "drone", "yield")


def validate_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """Validate the three overlay weights and return a normalized-key copy."""
    if set(weights) != set(LAYER_NAMES):
        raise ValueError(f"Weights must contain exactly: {', '.join(LAYER_NAMES)}")
    values = {name: float(weights[name]) for name in LAYER_NAMES}
    if any(not np.isfinite(value) or value < 0 for value in values.values()):
        raise ValueError("Layer weights must be finite and non-negative")
    if not np.isclose(sum(values.values()), 1.0, atol=1e-8):
        raise ValueError(f"Layer weights must sum to 1.0; got {sum(values.values()):.8f}")
    return values


def normalize_minmax(values: np.ndarray) -> np.ndarray:
    """Min-max scale finite values to [0, 1], preserving missing cells as NaN."""
    array = np.asarray(values, dtype=float)
    finite = np.isfinite(array)
    if not finite.any():
        raise ValueError("Cannot normalize a layer with no finite values")
    minimum = float(np.min(array[finite]))
    maximum = float(np.max(array[finite]))
    normalized = np.full(array.shape, np.nan, dtype=float)
    if np.isclose(maximum, minimum):
        normalized[finite] = 0.5
    else:
        normalized[finite] = (array[finite] - minimum) / (maximum - minimum)
    return normalized


def compute_productive_score(
    satellite: np.ndarray | None,
    drone: np.ndarray | None,
    yield_layer: np.ndarray | None,
    weights: Mapping[str, float],
) -> np.ndarray:
    """Calculate a per-cell weighted score, renormalizing around layer nodata."""
    validated = validate_weights(weights)
    layers = {"satellite": satellite, "drone": drone, "yield": yield_layer}
    shape: tuple[int, ...] | None = None
    normalized: dict[str, np.ndarray] = {}
    for name, layer in layers.items():
        if layer is None:
            if validated[name] > 0:
                raise ValueError(f"Layer {name!r} has a positive weight but no input data")
            continue
        values = np.asarray(layer, dtype=float)
        if shape is None:
            shape = values.shape
        elif values.shape != shape:
            raise ValueError("All overlay layers must have the same shape")
        if validated[name] > 0:
            normalized[name] = normalize_minmax(values)
    if shape is None or not normalized:
        raise ValueError("At least one available layer must have a positive weight")

    numerator = np.zeros(shape, dtype=float)
    denominator = np.zeros(shape, dtype=float)
    for name, values in normalized.items():
        valid = np.isfinite(values)
        numerator[valid] += validated[name] * values[valid]
        denominator[valid] += validated[name]
    score = np.full(shape, np.nan, dtype=float)
    available = denominator > 0
    score[available] = numerator[available] / denominator[available]
    return score
