# Decisions

## 2026-10-28 — Initial scaffold

### QGIS image tag
- **Choice:** `qgis/qgis:3.40.3` (LTR-class pin; bump only with explicit DECISIONS entry).
- **Why:** Stable LTR line, Python 3, processing providers present. Verify on first `docker build`.
- **Fallback order if tag missing:** `3.40.2` → `3.34.15` → record actual digest in this file.

### Python deps vs system GDAL
- **Choice:** venv with `--system-site-packages`; `constraints.txt` pins `gdal==0`, `numpy` from system only (no pip reinstall).
- **Pip packages:** pydantic v2, httpx, pystac-client, supabase, pytest, ruff, plus any missing analysis libs.
- **Apt preferred:** python3-rasterio, python3-geopandas, python3-shapely, python3-scipy, python3-sklearn, python3-psycopg2.

### IDW
- **Choice:** QGIS `qgis:idwinterpolation` in container; **scipy** inverse-distance fallback for host unit tests and QGIS failure.
- **Kriging:** deferred to v1.1.

### Weather provider
- **Choice:** Open-Meteo Historical API (no key for non-commercial). Interface isolated in `weather.py` so NASA POWER/ERA5 can swap later.
- **Licence:** re-check before commercial launch (Prompt 5).

### Worker host
- **Choice (dev):** docker-compose local. **Prod:** defer to Prompt 5 (Fly.io preferred for scale-to-zero).

### Shared config contract
- **Choice:** pydantic owns schema; `python -m pipeline.cli schema` writes `packages/shared/config.schema.json`; zod mirrors in `packages/shared`. CI drift check compares both.

### Monorepo package name
- **Choice:** npm scope `@fpz/shared` (Field Productivity Zones). App display name remains AgroMap in README until product rename.

## 2026-10-29 — Prompt 3 worker integration

### Pipeline isolation
- **Choice:** `subprocess` via `worker.pipeline_entry` with `JOB_TIMEOUT_SEC` (default 1800).
- **Fallback:** on `OSError` spawn failure (seen as WinError 50 under some Windows/pytest hosts) or `FPZ_PIPELINE_INLINE=1`, run in-process. Docker/Linux keeps real subprocess isolation.
- **Why:** QGIS segfault must not kill claim loop when isolation works. Windows has no soft RLIMIT; memory cap is container/cgroup (`JOB_MEMORY_MB`).

### DB access
- **Choice:** `psycopg_pool.ConnectionPool` for main loop; plain `connect()` for heartbeat thread (avoids pool cross-thread issues).

### E2E
- **Choice:** `make e2e` always runs offline `e2e_local.py`. `e2e_supabase.py` runs when env present; otherwise prints skip.
- **Why:** Clean checkout without Docker Supabase must still gate pipeline+worker entry.

### Yield column confirm
- **Choice:** `ingest_dataset` writes `meta.suggested_mapping`; UI `SuggestedMappingForm` writes `meta.confirmed_mapping` + `yield_column`.
- **Analysis** prefers confirmed mapping when present (handlers resolve paths only; pipeline config.yield.column still authoritative from form).
