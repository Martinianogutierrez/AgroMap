#!/usr/bin/env python3
"""
Full e2e against local Supabase (service role).
Requires: DATABASE_URL, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Seeds user, uploads synthetic datasets, queues analysis, runs one worker pass, checks shp.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

import geopandas as gpd
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.synthetic import write_synthetic_field  # noqa: E402
from worker import storage  # noqa: E402
from worker.handlers import handle_analysis  # noqa: E402


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"missing env {name}")
    return v


def main() -> int:
    db_url = _env("DATABASE_URL")
    _env("SUPABASE_SERVICE_ROLE_KEY")
    os.environ.setdefault("SUPABASE_URL", os.environ.get("NEXT_PUBLIC_SUPABASE_URL", ""))
    if not os.environ.get("SUPABASE_URL"):
        raise SystemExit("SUPABASE_URL or NEXT_PUBLIC_SUPABASE_URL required")

    owner_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    field_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    with tempfile.TemporaryDirectory(prefix="fpz-e2e-sb-") as td:
        td_path = Path(td)
        synth = write_synthetic_field(td_path / "field", n_seasons=2, seed=21)
        boundary = synth["workdir"] / "boundary.geojson"

        # create auth user + rows via SQL (service)
        with psycopg.connect(db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into auth.users (id, instance_id, aud, role, email,
                      encrypted_password, email_confirmed_at, raw_app_meta_data,
                      raw_user_meta_data, created_at, updated_at)
                    values (
                      %s::uuid, '00000000-0000-0000-0000-000000000000', 'authenticated',
                      'authenticated', %s, crypt('e2e-pass', gen_salt('bf')),
                      now(), '{"provider":"email","providers":["email"]}'::jsonb,
                      '{}'::jsonb, now(), now()
                    )
                    on conflict (id) do nothing
                    """,
                    (owner_id, f"e2e-{owner_id[:8]}@example.com"),
                )
                cur.execute(
                    """
                    insert into public.projects (id, owner_id, name)
                    values (%s::uuid, %s::uuid, 'e2e')
                    """,
                    (project_id, owner_id),
                )
                cur.execute(
                    """
                    insert into public.fields (id, project_id, name, crop_default, boundary)
                    values (
                      %s::uuid, %s::uuid, 'e2e-field', 'soybean',
                      ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)
                    )
                    """,
                    (
                        field_id,
                        project_id,
                        json.dumps(json.loads(boundary.read_text())["features"][0]["geometry"]),
                    ),
                )
            conn.commit()

            # upload yields
            for s in synth["inputs"]["seasons"]:
                ypath = synth["workdir"] / s["yield_path"]
                storage_path = f"{owner_id}/{field_id}/{ypath.name}"
                storage.upload_object("uploads", storage_path, ypath)
                ds_id = str(uuid.uuid4())
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        insert into public.datasets (
                          id, field_id, kind, season_year, crop,
                          storage_path, original_name, size_bytes, meta
                        )
                        values (
                          %s::uuid, %s::uuid, 'yield', %s, 'soybean',
                          %s, %s, %s, '{}'::jsonb
                        )
                        """,
                        (
                            ds_id,
                            field_id,
                            s["year"],
                            storage_path,
                            ypath.name,
                            ypath.stat().st_size,
                        ),
                    )
                conn.commit()

            cfg = json.loads(synth["config"].model_dump_json(by_alias=True))
            cfg["field_id"] = field_id
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.jobs
                      (id, field_id, owner_id, kind, config, status)
                    values (%s::uuid, %s::uuid, %s::uuid, 'analysis', %s::jsonb, 'running')
                    """,
                    (job_id, field_id, owner_id, Jsonb(cfg)),
                )
            conn.commit()

            job = {
                "id": job_id,
                "field_id": field_id,
                "owner_id": owner_id,
                "kind": "analysis",
                "config": cfg,
                "status": "running",
                "worker_id": "e2e",
            }
            handle_analysis(conn, job, td_path / "work")

            with conn.cursor() as cur:
                cur.execute("select status, error from public.jobs where id = %s::uuid", (job_id,))
                row = cur.fetchone()
            assert row["status"] == "succeeded", row

            with conn.cursor() as cur:
                cur.execute(
                    """
                    select storage_path from public.job_outputs
                    where job_id = %s::uuid and kind = 'shp_zip'
                    """,
                    (job_id,),
                )
                out = cur.fetchone()
            assert out, "no shp_zip output"

            dest = td_path / "out.zip"
            storage.download_object("outputs", out["storage_path"], dest)
            with zipfile.ZipFile(dest) as zf:
                zf.extractall(td_path / "shp")
            gdf = gpd.read_file(td_path / "shp" / "zones.shp")
            assert len(gdf) >= 1
            print("e2e_supabase OK", {"job_id": job_id, "zones": len(gdf)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
