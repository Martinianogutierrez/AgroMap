from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np

from pipeline.errors import PipelineError
from pipeline.ingest import write_grid_geotiff

ReportFn = Callable[[str, int, str], None]


def ndvi_from_nir_red(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    nir = nir.astype("float32")
    red = red.astype("float32")
    denom = nir + red
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(denom != 0, (nir - red) / denom, np.nan)
    return out.astype("float32")


def vari_from_rgb(r: np.ndarray, g: np.ndarray, b: np.ndarray) -> np.ndarray:
    r, g, b = r.astype("float32"), g.astype("float32"), b.astype("float32")
    denom = g + r - b
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(denom != 0, (g - r) / denom, np.nan)
    return out.astype("float32")


def drone_index(
    path: Path,
    out_tif: Path,
    *,
    nir_band: int | None = 4,
    red_band: int | None = 3,
    report: ReportFn | None = None,
) -> tuple[Path, dict]:
    import rasterio

    with rasterio.open(path) as src:
        meta = {"bands": src.count, "crs": str(src.crs)}
        if src.count >= 4 and nir_band and red_band:
            nir = src.read(nir_band).astype("float32")
            red = src.read(red_band).astype("float32")
            idx = ndvi_from_nir_red(nir, red)
            flag = {"index": "ndvi", "vari_fallback": False}
        elif src.count >= 3:
            r, g, b = src.read(1), src.read(2), src.read(3)
            idx = vari_from_rgb(r, g, b)
            flag = {"index": "vari", "vari_fallback": True}
        else:
            raise PipelineError("drone raster needs ≥3 bands for VARI or ≥4 for NDVI")
        write_grid_geotiff(idx, src.transform, src.crs, out_tif)
    if report:
        report("ndvi", 55, f"drone {flag['index']} → {out_tif.name}")
    return out_tif, {**meta, **flag}


def fake_ndvi_grid(shape: tuple[int, int], seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    base = 0.3 + 0.4 * (xx / max(shape[1] - 1, 1))
    noise = rng.normal(0, 0.05, size=shape)
    return np.clip(base + noise, -1, 1).astype("float32")
