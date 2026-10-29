from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import Point, box

from pipeline.config import AnalysisConfig
from pipeline.crs_util import utm_crs_from_geom
from pipeline.weather import season_window, synthetic_daily


def make_boundary(lon: float = -60.0, lat: float = -33.0, size_deg: float = 0.02):
    return box(lon, lat, lon + size_deg, lat + size_deg * 0.8)


def write_synthetic_field(
    workdir: Path,
    n_seasons: int = 3,
    seed: int = 42,
    field_id: str = "11111111-1111-1111-1111-111111111111",
) -> dict:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    boundary = make_boundary()
    b_gdf = gpd.GeoDataFrame(geometry=[boundary], crs="EPSG:4326")
    b_path = workdir / "boundary.geojson"
    b_gdf.to_file(b_path, driver="GeoJSON")

    utm = utm_crs_from_geom(boundary)
    b_utm = b_gdf.to_crs(utm)
    minx, miny, maxx, maxy = b_utm.total_bounds

    seasons = []
    weather_fixtures: dict[int, object] = {}
    base_year = 2019
    for i in range(n_seasons):
        year = base_year + i
        # spatial trend + noise + outliers
        n = 400
        xs = rng.uniform(minx, maxx, n)
        ys = rng.uniform(miny, maxy, n)
        trend = 3000 + 1500 * ((xs - minx) / (maxx - minx + 1e-9))
        noise = rng.normal(0, 200, n)
        yld = trend + noise
        # inject outliers
        yld[:5] = yld[:5] * 5
        yld[5:8] = 0
        gdf = gpd.GeoDataFrame(
            {"VRYIELDVOL": yld, "geometry": [Point(x, y) for x, y in zip(xs, ys, strict=False)]},
            crs=utm,
        )
        # clip to boundary
        gdf = gdf[gdf.within(b_utm.geometry.iloc[0])].copy()
        ypath = workdir / f"yield_{year}.gpkg"
        gdf.to_file(ypath, driver="GPKG")
        seasons.append({"year": year, "yield_path": ypath.name})

        # weather: make year 0 dry so above_mean may exclude it depending on scale
        scale = 0.4 if i == 0 else 1.0 + 0.2 * i
        start, end = season_window(year, "soybean", lat=boundary.centroid.y)
        weather_fixtures[year] = synthetic_daily(start, end, precip_scale=scale, seed=seed + i)

    # serialize weather fixtures to records for JSON
    wf_json = {
        str(y): df.assign(
            time=df["time"].astype(str)
        ).to_dict(orient="records")
        for y, df in weather_fixtures.items()
    }
    # keep DataFrames for in-process tests
    inputs = {
        "boundary": str(b_path.name),
        "seasons": seasons,
        "weather_fixtures": weather_fixtures,
        "weather_fixtures_json": wf_json,
    }
    (workdir / "inputs.json").write_text(
        json.dumps(
            {
                "boundary": b_path.name,
                "seasons": seasons,
                # CLI run without network: disable weather or pass via side channel;
                # store flag so tests can reload fixtures
                "use_offline_weather": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    # pickle-free fixture store
    import pickle

    with (workdir / "weather_fixtures.pkl").open("wb") as f:
        pickle.dump(weather_fixtures, f)

    cfg = AnalysisConfig(
        field_id=field_id,
        seasons={
            "include": "all",
            "crop": "soybean",
            "weather_filter": {
                "enabled": False,
                "metric": "precip_sum_mm",
                "min_seasons": 2,
                "rule": {"op": "above_mean", "tolerance_pct": 0},
            },
        },
        layers={"use_yield": True, "use_ndvi_satellite": True, "use_ndvi_drone": False},
        zones={"k": 3, "k_min": 3, "k_max": 5, "min_area_ha": 0.01, "smooth_px": 1},
        resolution_m=20,
        export={"shapefile": True, "gpkg": True, "qgz": False, "preview_png": True},
        random_seed=seed,
    )
    (workdir / "config.json").write_text(
        cfg.model_dump_json(by_alias=True, indent=2), encoding="utf-8"
    )
    return {"workdir": workdir, "config": cfg, "inputs": inputs, "boundary": b_gdf}
