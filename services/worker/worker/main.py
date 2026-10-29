from __future__ import annotations

import json
import logging
import os
import signal
import tempfile
import threading
import time
import traceback
from pathlib import Path

from pipeline.config import load_config
from pipeline.errors import PipelineError
from pipeline.run import run
from worker import db, storage

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("worker")

STOP = threading.Event()


def _handle_sigterm(*_: object) -> None:
    log.info("shutdown signal")
    STOP.set()


def heartbeat_loop(conn_factory, job_id: str, worker_id: str) -> None:
    while not STOP.is_set():
        try:
            with conn_factory() as conn:
                db.heartbeat(conn, job_id, worker_id)
        except Exception:
            log.exception("heartbeat failed")
        STOP.wait(float(os.environ.get("HEARTBEAT_SEC", "30")))


def handle_analysis(conn, job: dict) -> None:
    job_id = str(job["id"])
    raw_cfg = job["config"]
    if not isinstance(raw_cfg, dict):
        raw_cfg = json.loads(raw_cfg)
    cfg = load_config(raw_cfg)
    throttle = db.ProgressThrottler()

    def report(step: str, pct: int, msg: str) -> None:
        throttle.maybe_update(conn, job_id, step=step, progress=pct, log_line=msg)

    def is_canceled() -> bool:
        row = db.get_job(conn, job_id)
        return bool(row and row["status"] == "canceled")

    with tempfile.TemporaryDirectory(prefix=f"fpz-{job_id}-") as td:
        workdir = Path(td)
        # MVP: optional inline _inputs. Prompt 3 resolves datasets from DB.
        inputs = raw_cfg.get("_inputs") if isinstance(raw_cfg, dict) else None
        if not inputs:
            raise PipelineError(
                "job missing inline _inputs; dataset download lands in Prompt 3"
            )
        outs = run(cfg, inputs, workdir / "out", report=report, is_canceled=is_canceled)
        owner = str(job["owner_id"])
        uploaded = []
        mapping = [
            ("shp_zip", outs.shp_zip, "application/zip"),
            ("gpkg", outs.gpkg, "application/geopackage+sqlite3"),
            ("zones_tif", outs.zones_tif, "image/tiff"),
            ("preview_png", outs.preview_png, "image/png"),
            ("report_json", outs.report_json, "application/json"),
            ("params_json", outs.params_json, "application/json"),
        ]
        for kind, path, ctype in mapping:
            if path and path.exists():
                dest = f"{owner}/{job_id}/{path.name}"
                storage.upload_object("outputs", dest, path, ctype)
                uploaded.append(
                    {"kind": kind, "storage_path": dest, "size_bytes": path.stat().st_size}
                )
        db.replace_outputs(conn, job_id, uploaded)
        if outs.zones_gdf is not None:
            gdf = outs.zones_gdf.to_crs(4326)
            fc = json.loads(gdf.to_json())
            db.replace_zones(conn, job_id, fc.get("features") or [])
        db.finish_job(conn, job_id, status="succeeded", log_extra="done")


def process_one(conn, job: dict) -> None:
    kind = job.get("kind") or "analysis"
    try:
        if kind == "analysis":
            handle_analysis(conn, job)
        else:
            raise PipelineError(f"unknown job kind: {kind}")
    except PipelineError as e:
        log.warning("job %s failed: %s", job["id"], e.message)
        db.finish_job(conn, str(job["id"]), status="failed", error=e.message)
    except Exception as e:
        log.error("job %s crashed: %s", job["id"], e)
        db.finish_job(
            conn,
            str(job["id"]),
            status="failed",
            error="internal error",
            log_extra=traceback.format_exc()[-4000:],
        )


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_sigterm)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _handle_sigterm)
    worker_id = os.environ.get("WORKER_ID", f"worker-{os.getpid()}")
    log.info("worker start id=%s", worker_id)
    while not STOP.is_set():
        try:
            with db.connect() as conn:
                job = db.claim_job(conn, worker_id)
                if not job:
                    STOP.wait(2.0)
                    continue
                log.info("claimed job %s", job["id"])
                t = threading.Thread(
                    target=heartbeat_loop,
                    args=(db.connect, str(job["id"]), worker_id),
                    daemon=True,
                )
                t.start()
                process_one(conn, job)
        except Exception:
            log.exception("loop error")
            time.sleep(3)


if __name__ == "__main__":
    main()
