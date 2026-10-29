from __future__ import annotations

import logging
import os
import signal
import tempfile
import threading
import time
import traceback
from pathlib import Path

from pipeline.errors import PipelineError
from worker import db
from worker.handlers import handle_analysis, handle_ingest_boundary, handle_ingest_dataset
from worker.health import set_state, start_health_server
from worker.logging_setup import maybe_init_sentry, setup_logging

setup_logging()
maybe_init_sentry()
log = logging.getLogger("worker")

STOP = threading.Event()
_jobs_done = 0


def _handle_sigterm(*_: object) -> None:
    log.info("shutdown signal")
    STOP.set()


def heartbeat_loop(job_id: str, worker_id: str, stop: threading.Event) -> None:
    while not stop.is_set() and not STOP.is_set():
        try:
            with db.connect() as conn:
                db.heartbeat(conn, job_id, worker_id)
        except Exception:
            log.exception("heartbeat failed", extra={"job_id": job_id})
        stop.wait(float(os.environ.get("HEARTBEAT_SEC", "30")))


def process_one(conn, job: dict) -> None:
    kind = job.get("kind") or "analysis"
    job_id = str(job["id"])
    td = tempfile.TemporaryDirectory(prefix=f"fpz-{job_id}-")
    try:
        workdir = Path(td.name)
        if kind == "analysis":
            handle_analysis(conn, job, workdir)
        elif kind == "ingest_boundary":
            handle_ingest_boundary(conn, job, workdir)
        elif kind == "ingest_dataset":
            handle_ingest_dataset(conn, job, workdir)
        else:
            raise PipelineError(f"unknown job kind: {kind}")
    except PipelineError as e:
        log.warning(
            "job failed: %s",
            e.message,
            extra={"job_id": job_id},
        )
        db.finish_job(conn, job_id, status="failed", error=e.message)
    except Exception as e:
        log.error(
            "job crashed: %s",
            e,
            extra={"job_id": job_id},
            exc_info=True,
        )
        db.finish_job(
            conn,
            job_id,
            status="failed",
            error="internal error",
            log_extra=traceback.format_exc()[-4000:],
        )
    finally:
        td.cleanup()


def main() -> None:
    global _jobs_done
    signal.signal(signal.SIGTERM, _handle_sigterm)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _handle_sigterm)

    worker_id = os.environ.get("WORKER_ID", f"worker-{os.getpid()}")
    set_state(ok=True, worker_id=worker_id, jobs_done=0)
    start_health_server()
    log.info("worker start", extra={"worker_id": worker_id})

    reap_every = float(os.environ.get("REAP_EVERY_SEC", "60"))
    last_reap = 0.0

    while not STOP.is_set():
        try:
            now = time.monotonic()
            if now - last_reap > reap_every:
                with db.connection() as conn:
                    reaped = db.reap_stale(conn)
                    if reaped:
                        log.info("reaped %s stale jobs", len(reaped))
                last_reap = now

            with db.connection() as conn:
                job = db.claim_job(conn, worker_id)
                if not job or not job.get("id"):
                    STOP.wait(2.0)
                    continue
                job_id = str(job["id"])
                log.info("claimed job", extra={"job_id": job_id, "worker_id": worker_id})
                hb_stop = threading.Event()
                t = threading.Thread(
                    target=heartbeat_loop,
                    args=(job_id, worker_id, hb_stop),
                    daemon=True,
                )
                t.start()
                try:
                    process_one(conn, job)
                finally:
                    hb_stop.set()
                _jobs_done += 1
                set_state(jobs_done=_jobs_done)
        except Exception:
            log.exception("loop error")
            time.sleep(3)


if __name__ == "__main__":
    main()
