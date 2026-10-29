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
