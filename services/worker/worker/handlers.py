from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pipeline.config import load_config
from pipeline.errors import PipelineError
from worker import db, storage
from worker.ingest_handlers import inspect_dataset, set_field_boundary_from_file
from worker.resolve_inputs import build_pipeline_inputs
from worker.subprocess_run import run_pipeline_subprocess

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    "shp_zip": "application/zip",
    "gpkg": "application/geopackage+sqlite3",
    "zones_tif": "image/tiff",
    "preview_png": "image/png",
    "report_json": "application/json",
    "params_json": "application/json",
}


def handle_ingest_dataset(conn, job: dict, workdir: Path) -> None:
    cfg = job.get("config") or {}
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    dataset_id = cfg.get("dataset_id")
    if not dataset_id:
        raise PipelineError("ingest_dataset config requires dataset_id")
    ds = db.get_dataset(conn, str(dataset_id))
    if not ds:
        raise PipelineError("dataset not found")
    dest = workdir / Path(ds["storage_path"]).name
    storage.download_object("uploads", ds["storage_path"], dest)
    meta = inspect_dataset(dest, kind=str(ds["kind"]))
    if meta.get("crs_missing") and ds["kind"] in ("yield", "boundary", "orthomosaic"):
        # still store suggestion; fail job with actionable error for geo kinds needing CRS
        db.update_dataset_meta(conn, str(dataset_id), meta)
        if ds["kind"] != "yield" or meta.get("type") != "csv":
            raise PipelineError(
                meta.get("action")
                or "file has no CRS; set EPSG on the dataset form and re-upload"
            )
    db.update_dataset_meta(conn, str(dataset_id), meta)
    db.finish_job(
        conn,
        str(job["id"]),
        status="succeeded",
        log_extra=(
            f"ingest_dataset ok features={meta.get('feature_count')} "
            f"bands={meta.get('bands')}"
        ),
    )


def handle_ingest_boundary(conn, job: dict, workdir: Path) -> None:
    cfg = job.get("config") or {}
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    field_id = cfg.get("field_id") or job.get("field_id")
    storage_path = cfg.get("storage_path")
    if not field_id or not storage_path:
        raise PipelineError("ingest_boundary needs field_id and storage_path")
    dest = workdir / "boundary_in"
    storage.download_object("uploads", storage_path, dest)
    geom = set_field_boundary_from_file(dest)
    db.update_field_boundary(conn, str(field_id), geom)
    db.finish_job(conn, str(job["id"]), status="succeeded", log_extra="boundary set")


def handle_analysis(conn, job: dict, workdir: Path) -> None:
    job_id = str(job["id"])
    raw_cfg = job["config"]
    if not isinstance(raw_cfg, dict):
        raw_cfg = json.loads(raw_cfg)
    # strip internal keys before pydantic
    inline = raw_cfg.pop("_inputs", None)
    cfg = load_config(raw_cfg)
    field_id = str(job["field_id"])
    field = db.get_field(conn, field_id)
    if not field:
        raise PipelineError("field not found")

    throttle = db.ProgressThrottler(min_interval=0.5)

    def report(step: str, pct: int, msg: str) -> None:
        throttle.maybe_update(conn, job_id, step=step, progress=pct, log_line=msg)
        log.info(msg, extra={"job_id": job_id, "step": step})

    def is_canceled() -> bool:
        row = db.get_job(conn, job_id)
        return bool(row and row["status"] == "canceled")

    if is_canceled():
        raise PipelineError("job canceled")

    report("resolve", 5, "resolve datasets")
    if inline:
        inputs = inline
    else:
        datasets = db.list_datasets_for_field(conn, field_id)
        # prefer user-confirmed yield column from dataset meta
        for d in datasets:
            meta = d.get("meta") or {}
            conf = meta.get("confirmed_mapping") or {}
            col = conf.get("yield") or meta.get("yield_column")
            if col and d.get("kind") == "yield":
                # mutate config yield column if user confirmed
                raw_cfg.setdefault("yield", {})
                if isinstance(raw_cfg.get("yield"), dict) and not raw_cfg["yield"].get(
                    "_locked"
                ):
                    raw_cfg["yield"]["column"] = col
                    cfg = load_config(raw_cfg)

        def dl(bucket: str, path: str, dest: Path) -> Path:
            return storage.download_object(bucket, path, dest)

        inputs = build_pipeline_inputs(
            field=field,
            datasets=datasets,
            config=cfg,
            workdir=workdir,
            download_fn=dl,
        )

    if is_canceled():
        raise PipelineError("job canceled")

    report("pipeline", 10, "run pipeline subprocess")
    # attach weather fixtures from synthetic e2e if present on disk next to boundary
    result = run_pipeline_subprocess(
        config=cfg.model_dump(by_alias=True),
        inputs=inputs,
        workdir=workdir / "sub",
    )

    if is_canceled():
        raise PipelineError("job canceled")

    owner = str(job["owner_id"])
    uploaded: list[dict[str, Any]] = []
    kind_paths = [
        ("shp_zip", result.get("shp_zip")),
        ("gpkg", result.get("gpkg")),
        ("zones_tif", result.get("zones_tif")),
        ("preview_png", result.get("preview_png")),
        ("report_json", result.get("report_json")),
        ("params_json", result.get("params_json")),
    ]
    for kind, p in kind_paths:
        if not p:
            continue
        path = Path(p)
        if not path.exists():
            continue
        dest = f"{owner}/{job_id}/{path.name}"
        storage.upload_object(
            "outputs", dest, path, CONTENT_TYPES.get(kind, "application/octet-stream")
        )
        uploaded.append(
            {"kind": kind, "storage_path": dest, "size_bytes": path.stat().st_size}
        )

    db.replace_outputs(conn, job_id, uploaded)

    zones_fc = result.get("zones_geojson") or {}
    features = zones_fc.get("features") if isinstance(zones_fc, dict) else None
    if features:
        db.replace_zones(conn, job_id, features)

    summary = ""
    if result.get("report_json") and Path(result["report_json"]).exists():
        try:
            rep = json.loads(Path(result["report_json"]).read_text(encoding="utf-8"))
            summary = (
                f"n_zones={rep.get('n_zones')} timings={rep.get('timings')} "
                f"seasons={list((rep.get('seasons') or {}).keys())}"
            )
        except Exception:
            summary = "report written"

    report("export", 100, summary or "done")
    db.finish_job(conn, job_id, status="succeeded", log_extra=summary or "done")
