from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np

from pipeline.config import AnalysisConfig, load_config
from pipeline.crs_util import utm_crs_from_geom
from pipeline.errors import PipelineError
from pipeline.export import (
    export_gpkg,
    export_preview_png,
    export_shapefile_zip,
    export_zones_tif,
    write_params,
    write_report,
)
from pipeline.ingest import load_boundary
from pipeline.interpolate import interpolate_idw
from pipeline.ndvi import fake_ndvi_grid
from pipeline.normalize import stack_mean_cv, zscore
from pipeline.weather import filter_seasons
from pipeline.yield_clean import clean_yield_gdf
from pipeline.zones import cluster_layers, majority_filter, polygonize_zones, zone_stats

ReportFn = Callable[[str, int, str], None]


def _noop(step: str, pct: int, msg: str) -> None:
    return None


@dataclass
class Outputs:
    workdir: Path
    shp_zip: Path | None = None
    gpkg: Path | None = None
    zones_tif: Path | None = None
    preview_png: Path | None = None
    report_json: Path | None = None
    params_json: Path | None = None
    zones_gdf: gpd.GeoDataFrame | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def run(
    config: AnalysisConfig | dict,
    inputs: dict[str, Any],
    workdir: str | Path,
    report: ReportFn | None = None,
    is_canceled: Callable[[], bool] | None = None,
) -> Outputs:
    """Fixed-order pipeline. `inputs` is local paths + optional fixtures (no network required)."""
    cfg = load_config(config)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    rep: ReportFn = report or _noop
    timings: dict[str, float] = {}
    report_data: dict[str, Any] = {"seasons": {}, "removals": {}, "timings": timings}

    def check_cancel() -> None:
        if is_canceled and is_canceled():
            raise PipelineError("job canceled")

    t0 = time.time()
    rep("validate", 5, "validate config + boundary")
    boundary = load_boundary(inputs["boundary"])
    utm = utm_crs_from_geom(boundary.geometry.iloc[0])
    boundary_utm = boundary.to_crs(utm)
    centroid = boundary.geometry.iloc[0].centroid
    lat, lon = centroid.y, centroid.x

    seasons_meta: list[dict[str, Any]] = list(inputs.get("seasons") or [])
    if not seasons_meta:
        raise PipelineError("inputs.seasons required (list of {year, yield_path?})")
    years = [int(s["year"]) for s in seasons_meta]

    if cfg.seasons.include != "all":
        allow = set(cfg.seasons.include)
        years = [y for y in years if y in allow]
        seasons_meta = [s for s in seasons_meta if int(s["year"]) in allow]

    check_cancel()
    wf = cfg.seasons.weather_filter.model_dump() if cfg.seasons.weather_filter else None
    fixtures = inputs.get("weather_fixtures")
    included, decisions = filter_seasons(
        years,
        lat=lat,
        lon=lon,
        crop=cfg.seasons.crop,
        weather_filter=wf,
        cache_dir=workdir / ".cache_weather",
        fixtures=fixtures,
        report=rep,
    )
    report_data["seasons"] = decisions
    seasons_meta = [s for s in seasons_meta if int(s["year"]) in set(included)]
    timings["seasons"] = time.time() - t0

    check_cancel()
    yld_norm_layers: list[np.ndarray] = []
    yld_raw_layers: list[np.ndarray] = []
    transform = None
    crs = utm
    grid_shape = None

    if cfg.layers.use_yield:
        t1 = time.time()
        for s in seasons_meta:
            year = int(s["year"])
            ypath = Path(s["yield_path"])
            gdf = gpd.read_file(ypath)
            if gdf.crs is None:
                raise PipelineError(f"yield {ypath.name} missing CRS")
            gdf = gdf.to_crs(utm)
            cleaned, crep = clean_yield_gdf(
                gdf,
                column=cfg.yield_.column,
                unit=cfg.yield_.unit,
                outlier_sigma=cfg.yield_.outlier_sigma,
                remove_zeros=cfg.yield_.remove_zeros,
                boundary_utm=boundary_utm,
                report=rep,
            )
            report_data["removals"][str(year)] = crep.__dict__
            out_tif = workdir / f"yield_{year}.tif"
            interpolate_idw(
                cleaned,
                "_yield_kg_ha",
                boundary_utm,
                cfg.resolution_m,
                out_tif,
                report=rep,
            )
            import rasterio

            with rasterio.open(out_tif) as src:
                arr = src.read(1)
                transform = src.transform
                crs = src.crs
                grid_shape = arr.shape
            yld_raw_layers.append(arr)
            yld_norm_layers.append(zscore(arr))
        timings["yield"] = time.time() - t1

    check_cancel()
    ndvi_layers: list[np.ndarray] = []
    if cfg.layers.use_ndvi_satellite:
        t2 = time.time()
        if grid_shape is None:
            # build empty grid from boundary
            minx, miny, maxx, maxy = boundary_utm.total_bounds
            w = max(1, int(np.ceil((maxx - minx) / cfg.resolution_m)))
            h = max(1, int(np.ceil((maxy - miny) / cfg.resolution_m)))
            grid_shape = (h, w)
            from rasterio.transform import from_origin

            transform = from_origin(minx, maxy, cfg.resolution_m, cfg.resolution_m)
        ndvi_fix = inputs.get("ndvi_fixtures") or {}
        for s in seasons_meta:
            year = int(s["year"])
            if year in ndvi_fix:
                nd = np.array(ndvi_fix[year], dtype="float32")
                if nd.shape != grid_shape:
                    # simple center crop/pad
                    nd = _match_shape(nd, grid_shape)
            else:
                nd = fake_ndvi_grid(grid_shape, seed=cfg.random_seed + year)
            ndvi_layers.append(nd)
        timings["ndvi"] = time.time() - t2

    check_cancel()
    rep("stack", 70, "normalize stack + stability")
    features: list[np.ndarray] = []
    yld_mean = yld_cv = ndvi_mean = None
    if yld_norm_layers:
        yld_mean, yld_cv = stack_mean_cv(yld_norm_layers)
        features.append(yld_mean)
        features.append(np.nan_to_num(yld_cv, nan=0.0))
    if ndvi_layers:
        ndvi_mean, _ = stack_mean_cv([zscore(a) for a in ndvi_layers])
        features.append(ndvi_mean)

    if not features:
        raise PipelineError("no layers enabled or produced")

    valid = np.ones(features[0].shape, dtype=bool)
    for f in features:
        valid &= np.isfinite(f)
    # mask to boundary
    from rasterio.features import geometry_mask

    assert transform is not None
    bmask = geometry_mask(
        [boundary_utm.geometry.iloc[0]],
        out_shape=features[0].shape,
        transform=transform,
        invert=True,
    )
    valid &= bmask

    rep("zones", 80, "k-means clustering")
    labels = cluster_layers(
        features,
        valid,
        k=cfg.zones.k,
        k_min=cfg.zones.k_min,
        k_max=cfg.zones.k_max,
        seed=cfg.random_seed,
        yield_mean=yld_mean if yld_mean is not None else features[0],
    )
    labels = majority_filter(labels, size=max(1, cfg.zones.smooth_px))

    zones_gdf = polygonize_zones(labels, transform, crs, cfg.zones.min_area_ha)
    zones_gdf = zone_stats(zones_gdf, yld_mean, yld_cv, ndvi_mean, transform)
    zones_gdf["n_seasons"] = len(seasons_meta)

    outs = Outputs(workdir=workdir, zones_gdf=zones_gdf)
    if cfg.export.shapefile:
        outs.shp_zip = export_shapefile_zip(
            zones_gdf, len(seasons_meta), workdir / "zones_shp.zip"
        )
    if cfg.export.gpkg:
        outs.gpkg = export_gpkg(zones_gdf, workdir / "zones.gpkg")
    outs.zones_tif = export_zones_tif(labels, transform, crs, workdir / "zones.tif")
    if cfg.export.preview_png:
        outs.preview_png = export_preview_png(labels, workdir / "preview.png")

    report_data["n_zones"] = int(zones_gdf["zone_id"].nunique())
    report_data["crs"] = str(crs)
    report_data["bbox"] = list(boundary.total_bounds)
    outs.report_json = write_report(workdir / "report.json", report_data)
    outs.params_json = write_params(
        workdir / "params.json",
        cfg.model_dump(by_alias=True),
    )
    rep("export", 100, "done")
    timings["total"] = time.time() - t0
    write_report(workdir / "report.json", report_data)
    return outs


def _match_shape(arr: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    out = np.full(shape, np.nan, dtype="float32")
    h = min(arr.shape[0], shape[0])
    w = min(arr.shape[1], shape[1])
    out[:h, :w] = arr[:h, :w]
    return out
