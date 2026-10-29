#!/usr/bin/env python3
"""Generate sample yield CSV / zipped SHP / multi-band GeoTIFF fixtures."""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point, box


def write_john_deere_csv(path: Path, n: int = 200, seed: int = 1) -> Path:
    rng = np.random.default_rng(seed)
    lon0, lat0 = -60.05, -33.10
    df = pd.DataFrame(
        {
            "Longitude": lon0 + rng.uniform(0, 0.02, n),
            "Latitude": lat0 + rng.uniform(0, 0.015, n),
            "VRYIELDVOL": rng.normal(3200, 400, n),
            "Speed": rng.normal(5.5, 0.4, n),
            "SwathWidth": np.full(n, 12.0),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def write_case_ih_csv(path: Path, n: int = 150, seed: int = 2) -> Path:
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(
        {
            "lon": -60.0 + rng.uniform(0, 0.02, n),
            "lat": -33.0 + rng.uniform(0, 0.015, n),
            "Yld_Mass_Dry": rng.normal(3.1, 0.3, n),  # t/ha style
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def write_trimble_csv(path: Path, n: int = 150, seed: int = 3) -> Path:
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(
        {
            "GpsLongitude": -60.0 + rng.uniform(0, 0.02, n),
            "GpsLatitude": -33.0 + rng.uniform(0, 0.015, n),
            "Dry_Yield": rng.normal(3000, 350, n),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def write_yield_shp_zip(path: Path, n: int = 120, seed: int = 4) -> Path:
    """Geographic CRS shapefile inside zip."""
    rng = np.random.default_rng(seed)
    lon = -60.0 + rng.uniform(0, 0.02, n)
    lat = -33.0 + rng.uniform(0, 0.015, n)
    gdf = gpd.GeoDataFrame(
        {
            "YLD": rng.normal(3100, 300, n),
            "geometry": [Point(x, y) for x, y in zip(lon, lat, strict=True)],
        },
        crs="EPSG:4326",
    )
    tmp = path.parent / "_shp_build"
    tmp.mkdir(parents=True, exist_ok=True)
    shp = tmp / "yield.shp"
    gdf.to_file(shp)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in tmp.glob("yield.*"):
            zf.write(p, arcname=p.name)
    for p in tmp.glob("yield.*"):
        p.unlink()
    tmp.rmdir()
    return path


def write_nir_geotiff(path: Path, size: int = 32, seed: int = 5) -> Path:
    import rasterio
    from rasterio.transform import from_origin

    rng = np.random.default_rng(seed)
    # B,G,R,NIR
    data = rng.integers(0, 255, size=(4, size, size), dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=size,
        width=size,
        count=4,
        dtype="uint8",
        crs="EPSG:32721",
        transform=from_origin(500000, 6300000, 10, 10),
    ) as dst:
        dst.write(data)
    return path


def write_boundary(path: Path) -> Path:
    gdf = gpd.GeoDataFrame(
        geometry=[box(-60.05, -33.10, -60.03, -33.085)],
        crs="EPSG:4326",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(path, driver="GeoJSON")
    return path


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "samples"
    write_john_deere_csv(root / "yield_john_deere.csv")
    write_case_ih_csv(root / "yield_case_ih.csv")
    write_trimble_csv(root / "yield_trimble.csv")
    write_yield_shp_zip(root / "yield_wgs84_shp.zip")
    write_nir_geotiff(root / "ortho_rgb_nir.tif")
    write_boundary(root / "boundary.geojson")
    print(f"samples written under {root}")


if __name__ == "__main__":
    main()
