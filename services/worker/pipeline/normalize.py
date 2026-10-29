from __future__ import annotations

import numpy as np


def zscore(arr: np.ndarray) -> np.ndarray:
    a = arr.astype("float64")
    m = np.nanmean(a)
    s = np.nanstd(a)
    if not np.isfinite(s) or s == 0:
        return np.zeros_like(a, dtype="float32")
    return ((a - m) / s).astype("float32")


def pct_of_mean(arr: np.ndarray) -> np.ndarray:
    a = arr.astype("float64")
    m = np.nanmean(a)
    if not np.isfinite(m) or m == 0:
        return np.zeros_like(a, dtype="float32")
    return ((a / m) * 100.0).astype("float32")


def stack_mean_cv(layers: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    if not layers:
        raise ValueError("no layers to stack")
    stack = np.stack(layers, axis=0)
    mean = np.nanmean(stack, axis=0).astype("float32")
    std = np.nanstd(stack, axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        cv = np.where(np.abs(mean) > 1e-6, std / np.abs(mean), np.nan).astype("float32")
    return mean, cv
