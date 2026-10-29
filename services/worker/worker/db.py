from __future__ import annotations

import json
import os
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row


def connect() -> psycopg.Connection:
    url = os.environ["DATABASE_URL"]
    return psycopg.connect(url, row_factory=dict_row)


def claim_job(conn: psycopg.Connection, worker_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute("select * from public.claim_job(%s)", (worker_id,))
        row = cur.fetchone()
    conn.commit()
    return row


def heartbeat(conn: psycopg.Connection, job_id: str, worker_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            update public.jobs
            set heartbeat_at = now()
            where id = %s::uuid and worker_id = %s and status = 'running'
            """,
            (job_id, worker_id),
        )
    conn.commit()


class ProgressThrottler:
    def __init__(self, min_interval: float = 0.5) -> None:
        self.min_interval = min_interval
        self._last = 0.0

    def maybe_update(
        self,
        conn: psycopg.Connection,
        job_id: str,
        *,
        step: str,
        progress: int,
        log_line: str | None = None,
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        if not force and now - self._last < self.min_interval:
            return
        self._last = now
        with conn.cursor() as cur:
            if log_line:
                cur.execute(
                    """
                    update public.jobs
                    set step = %s, progress = %s,
                        log = left(log || %s || E'\\n', 200000),
                        heartbeat_at = now()
                    where id = %s::uuid
                    """,
                    (step, progress, log_line, job_id),
                )
            else:
                cur.execute(
                    """
                    update public.jobs
                    set step = %s, progress = %s, heartbeat_at = now()
                    where id = %s::uuid
                    """,
                    (step, progress, job_id),
                )
        conn.commit()


def finish_job(
    conn: psycopg.Connection,
    job_id: str,
    *,
    status: str,
    error: str | None = None,
    log_extra: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            update public.jobs
            set status = %s::public.job_status,
                error = %s,
                finished_at = now(),
                progress = case when %s = 'succeeded' then 100 else progress end,
                log = case when %s is null then log else left(log || %s || E'\\n', 200000) end
            where id = %s::uuid
            """,
            (status, error, status, log_extra, log_extra, job_id),
        )
    conn.commit()


def get_job(conn: psycopg.Connection, job_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute("select * from public.jobs where id = %s::uuid", (job_id,))
        return cur.fetchone()


def replace_outputs(
    conn: psycopg.Connection, job_id: str, outputs: list[dict[str, Any]]
) -> None:
    with conn.cursor() as cur:
        cur.execute("delete from public.job_outputs where job_id = %s::uuid", (job_id,))
        for o in outputs:
            cur.execute(
                """
                insert into public.job_outputs (job_id, kind, storage_path, size_bytes)
                values (%s::uuid, %s, %s, %s)
                """,
                (job_id, o["kind"], o["storage_path"], o.get("size_bytes")),
            )
    conn.commit()


def replace_zones(
    conn: psycopg.Connection, job_id: str, zones_geojson_features: list[dict]
) -> None:
    """Insert zone features (props + GeoJSON geometry in EPSG:4326)."""
    with conn.cursor() as cur:
        cur.execute("delete from public.zones where job_id = %s::uuid", (job_id,))
        for f in zones_geojson_features:
            p = f.get("properties") or {}
            cur.execute(
                """
                insert into public.zones
                  (job_id, zone_id, zone_name, area_ha, yld_mean, yld_cv, ndvi_mean, geom)
                values (
                  %s::uuid, %s, %s, %s, %s, %s, %s,
                  ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)
                )
                """,
                (
                    job_id,
                    p.get("zone_id"),
                    p.get("zone_name"),
                    p.get("area_ha"),
                    p.get("yld_mean"),
                    p.get("yld_cv"),
                    p.get("ndvi_mean"),
                    json.dumps(f.get("geometry")),
                ),
            )
    conn.commit()
