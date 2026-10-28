# Field Productivity Zones — Project Plan (v2: scripted pipeline)

Web app (Next.js + Supabase) where users upload field data (drone orthomosaics, free satellite imagery, harvester yield maps), choose which seasons to include (optionally filtered by weather or crop), and get a productivity-zone map plus a downloadable shapefile.

**v2 change:** QGIS is no longer driven by an AI through an API/tool layer. A deterministic Python pipeline (PyQGIS headless + GDAL/rasterio/geopandas) runs from a single JSON config. The AI, if used at all, is a one-shot "text → config JSON" translator at the edge (Prompt 4), not an agent in the loop.

---

## 1. Is it viable? Is it easier?

Yes on both counts. Reasons:

| Concern | v1 (AI drives QGIS via tools) | v2 (scripted pipeline) |
|---|---|---|
| Reproducibility | Depends on model choices each run | Same config + same data = same result |
| Cost / latency | Many LLM calls per job | Zero LLM calls in the pipeline |
| Testing | Hard (non-deterministic) | Plain pytest on synthetic fields |
| Debugging | Trace tool calls | Run `python -m pipeline.cli run config.json` locally |
| Code surface | Tool registry, schemas, agent loop, MCP wrapper | One pipeline package + one worker |
| Failure modes | Wrong tool order, hallucinated params | Schema-validated config, fixed step order |

The workflow here (ingest → clean → interpolate → NDVI → normalize → cluster → export) is a fixed recipe, not an open-ended task, so an agent adds risk without adding value. What the user varies are parameters (seasons, number of zones, weather rule), which a form handles fine.

**Honest caveat on QGIS:** most steps are simpler and faster with `rasterio` / `geopandas` / `scikit-learn`. Use QGIS headless only where it earns its place: IDW interpolation, polygonize/smooth, styled map rendering (PNG + `.qgz`). That keeps the Docker image's QGIS dependency small and the tests fast.

---

## 2. Architecture

```
Browser (Next.js on Vercel)
   │  upload (tus resumable) ───────────────► Supabase Storage (private buckets)
   │  create analysis (config JSON) ─────────► Supabase Postgres + PostGIS  (jobs table = the queue)
   │  live progress  ◄──── Realtime on jobs row
   ▼
Worker (Docker, qgis/qgis base, own host: Fly.io / Hetzner / Railway)
   loop: claim job (FOR UPDATE SKIP LOCKED) → download inputs → pipeline.run(config) → upload outputs → update job
```

- No Redis, no BullMQ: the `jobs` table with `SKIP LOCKED` is the queue. Fewer moving parts.
- One job at a time per container; scale by adding replicas.
- Next.js on Vercel cannot run QGIS, so the worker needs its own host.
- The pipeline package is independent of Supabase: it takes a local workdir + config and writes outputs. The worker is a thin adapter. This makes local dev and tests trivial.

---

## 3. Tech decisions

- **Image:** `qgis/qgis:<PINNED_TAG>` (pin in Prompt 2, record in `docs/DECISIONS.md`).
- **Python env:** venv with `--system-site-packages` so it reuses the image's GDAL/numpy/PyQGIS; pip only for packages not in apt, with a `constraints.txt` that forbids reinstalling GDAL/numpy.
- **Libraries:** rasterio, geopandas, shapely, scipy, scikit-learn, pydantic v2, httpx, pystac-client, psycopg[binary], supabase-py (or plain signed-URL HTTP), pytest.
- **Satellite:** Sentinel-2 L2A from Earth Search STAC (public COGs, no auth). Use B04, B08 and SCL (cloud mask). Windowed reads, so no full-tile downloads.
- **Weather:** Open-Meteo Historical API (free, no key for non-commercial; check licence for commercial use).
- **Map UI:** MapLibre GL with COG/PNG overlays; zones served as GeoJSON from PostGIS.
- **Output CRS:** local UTM zone chosen automatically from field centroid; shapefile ships with `.prj`.

---

## 4. Repo layout

```
/apps/web                 Next.js (App Router, TypeScript)
/services/worker
   Dockerfile
   constraints.txt
   worker/
      main.py             claim loop, heartbeat, uploads
      db.py  storage.py
   pipeline/              pure Python, no Supabase imports
      qgis_runtime.py
      config.py           pydantic models (the job contract)
      ingest.py           detect type, reproject, to COG
      yield_clean.py
      interpolate.py
      ndvi.py             drone + Sentinel-2
      sentinel.py         STAC search + windowed read
      weather.py          Open-Meteo + season filter
      normalize.py
      zones.py            stack, k-means, smoothing, polygonize
      export.py           shp, gpkg, tif, png, qgz, report.json
      cli.py              python -m pipeline.cli run config.json --workdir ./out
   tests/                 synthetic field generator + pytest
/supabase/migrations
/docs  DECISIONS.md  RUNBOOK.md
docker-compose.yml  AGENTS.md  PROJECT_PLAN.md
```

---

## 5. Data model (Supabase / PostGIS)

```sql
create extension if not exists postgis;

create table projects (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  name text not null, created_at timestamptz default now()
);

create table fields (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  name text not null, crop_default text,
  boundary geometry(MultiPolygon, 4326),
  created_at timestamptz default now()
);

create type dataset_kind as enum ('orthomosaic','satellite','yield','boundary');

create table datasets (
  id uuid primary key default gen_random_uuid(),
  field_id uuid not null references fields(id) on delete cascade,
  kind dataset_kind not null,
  season_year int,                 -- harvest year
  crop text,
  storage_path text not null,      -- bucket/key
  original_name text, size_bytes bigint,
  meta jsonb default '{}',         -- crs, bands, bbox, yield column mapping, etc.
  created_at timestamptz default now()
);

create type job_status as enum ('queued','running','succeeded','failed','canceled');

create table jobs (
  id uuid primary key default gen_random_uuid(),
  field_id uuid not null references fields(id) on delete cascade,
  owner_id uuid not null references auth.users(id),
  kind text not null default 'analysis',
  config jsonb not null,           -- validated against pipeline/config.py
  status job_status not null default 'queued',
  step text, progress int default 0,
  log text default '', error text,
  worker_id text, heartbeat_at timestamptz,
  created_at timestamptz default now(), started_at timestamptz, finished_at timestamptz
);
create index on jobs (status, created_at);

create table job_outputs (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references jobs(id) on delete cascade,
  kind text not null,              -- shp_zip | gpkg | zones_tif | preview_png | qgz | report_json
  storage_path text not null, size_bytes bigint
);

create table zones (                -- for map display in the web app
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references jobs(id) on delete cascade,
  zone_id int, zone_name text, area_ha numeric,
  yld_mean numeric, yld_cv numeric, ndvi_mean numeric,
  geom geometry(MultiPolygon, 4326)
);
create index on zones using gist (geom);
```

**RLS (every table):** enabled; access via ownership chain `owner_id = auth.uid()` or `exists (select 1 from projects p where p.id = <fk> and p.owner_id = auth.uid())`. Storage policies: first path segment = `owner_id`. The worker uses the service role and never exposes it to the browser.

**Claiming a job (worker):**
```sql
update jobs set status='running', started_at=now(), heartbeat_at=now(), worker_id=%s
where id = (select id from jobs where status='queued' order by created_at
            for update skip locked limit 1)
returning *;
```
A reaper resets `running` jobs whose `heartbeat_at` is older than 2 minutes back to `queued` (max 2 retries).

---

## 6. Job contract (the config JSON)

```json
{
  "field_id": "uuid",
  "zones": {"k": "auto", "k_min": 3, "k_max": 5, "min_area_ha": 0.3, "smooth_px": 3},
  "layers": {"use_yield": true, "use_ndvi_satellite": true, "use_ndvi_drone": true},
  "seasons": {
    "include": "all",
    "crop": "soybean",
    "weather_filter": {
      "enabled": true,
      "metric": "precip_sum_mm",
      "window": {"mode": "crop_calendar", "start_md": "10-01", "end_md": "03-31"},
      "rule": {"op": "above_mean", "tolerance_pct": 0},
      "min_seasons": 3
    }
  },
  "yield": {"column": "VRYIELDVOL", "unit": "kg/ha", "outlier_sigma": 3, "remove_zeros": true},
  "resolution_m": 10,
  "export": {"shapefile": true, "gpkg": true, "qgz": true, "preview_png": true}
}
```

Rules: pydantic v2 models with `extra="forbid"`; the web form, the optional chat layer and the CLI all produce this same JSON; the worker rejects anything that fails validation before running a step.

`report.json` returned by every run records, for each season: included/excluded, the metric value, the field-wide mean it was compared with, and the reason. Zones are accompanied by n_seasons, mean yield, CV and NDVI.

---

## 7. Pipeline steps (fixed order, each reports progress)

1. **Validate** config, resolve datasets, create scratch dir.
2. **Ingest:** detect type, reproject to local UTM, clip to boundary, write COG (rasters) / GeoPackage (vectors). Orthomosaics: downsample overviews to `resolution_m` for analysis, keep the full-res COG for display.
3. **Season selection:** weather filter (section 8) → list of seasons used.
4. **Yield clean (per season):** map columns, drop zeros/nulls, remove speed/swath/edge-lag outliers where columns exist, `mean ± outlier_sigma·sd` and IQR filters, drop points outside boundary.
5. **Interpolate (per season):** QGIS `qgis:idwinterpolation` (or `gdal:gridinversedistance`) to `resolution_m`, mask to boundary.
6. **NDVI:** drone multispectral → (NIR−Red)/(NIR+Red); RGB only → VARI fallback and a flag in the report. Sentinel-2 → STAC search over the season window, cloud-mask with SCL, median composite of peak-growth scenes, resample to grid.
7. **Normalize per season:** z-score (or % of field mean) so wet and dry years are comparable.
8. **Stack and stability:** per-pixel mean of normalized yield, CV across seasons, mean NDVI.
9. **Cluster:** scale layers, k-means (k from config, or `auto` by silhouette between `k_min`..`k_max`), order clusters by mean yield → `Low / Medium / High` (or `Z1..Zk`).
10. **Clean zones:** majority filter, polygonize, dissolve, drop polygons under `min_area_ha` by merging into the largest neighbor.
11. **Export:** shapefile (zipped), GeoPackage, zones GeoTIFF, QGIS-rendered `preview.png`, `.qgz`, `report.json`, `params.json`.

**Shapefile field names (≤10 chars):** `ZONE_ID, ZONE_NAME, AREA_HA, YLD_MEAN, YLD_CV, NDVI_MEAN, N_SEASONS, GEN_DATE`. Include `.shp .shx .dbf .prj .cpg`.

---

## 8. Weather filter (deterministic)

```python
# pipeline/weather.py (sketch)
def fetch_daily(lat, lon, start, end) -> pd.DataFrame: ...   # Open-Meteo archive, cached on disk by key

def season_window(year, crop_cfg) -> tuple[date, date]: ...  # handles windows crossing the new year

def metric_value(df, metric, window) -> float:               # precip_sum_mm, gdd_base10, tmax_mean, heat_days_gt35, dry_spell_max
    ...

def apply_rule(values: dict[int, float], rule) -> dict[int, Decision]:
    # above_mean | below_mean | between(lo, hi) | top_n | bottom_n
    # Decision = {included: bool, value: float, reference: float, reason: str}
```

Behaviour:
- Reference mean is computed over the user's available seasons by default; optionally over a 20-year climatology (`reference: "climatology_20y"`).
- If fewer than `min_seasons` pass, the job fails fast with a clear message and the decision table, so the UI can ask the user to loosen the rule.
- Crop calendars live in `pipeline/crop_calendars.yaml` (soybean, corn, wheat, etc., hemisphere-aware). Users can override the window.
- Results are cached by (lat, lon, year range) to keep jobs fast and API-polite.

---

## 9. Docker and QGIS runtime

```dockerfile
# services/worker/Dockerfile
FROM qgis/qgis:PINNED_TAG
ENV QT_QPA_PLATFORM=offscreen PYTHONUNBUFFERED=1 PIP_BREAK_SYSTEM_PACKAGES=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3-pip python3-venv python3-rasterio python3-geopandas python3-shapely \
      python3-scipy python3-sklearn python3-psycopg2 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY constraints.txt requirements.txt ./
RUN python3 -m venv --system-site-packages /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -c constraints.txt -r requirements.txt
ENV PATH=/opt/venv/bin:$PATH
COPY . .
RUN useradd -m worker && chown -R worker /app
USER worker
CMD ["python", "-m", "worker.main"]
```

```python
# pipeline/qgis_runtime.py
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from contextlib import contextmanager

@contextmanager
def qgis_session():
    from qgis.core import QgsApplication
    app = QgsApplication([], False)
    app.initQgis()
    sys.path.append("/usr/share/qgis/python/plugins")
    import processing
    from processing.core.Processing import Processing
    from qgis.analysis import QgsNativeAlgorithms
    Processing.initialize()
    QgsApplication.processingRegistry().addProvider(QgsNativeAlgorithms())
    try:
        yield processing
    finally:
        app.exitQgis()
```

One `qgis_session()` per job process (QGIS does not like repeated init/exit in one process). If a QGIS crash risk appears, run the job in a subprocess so the worker survives.

---

## 10. Optional chat layer (not an agent)

Only after the pipeline works end to end. A single Claude API call from a Next.js route:

- Input: the user's message + current field metadata (available seasons, crops, datasets).
- Output: JSON conforming to the config schema (use structured output / a single "emit_config" tool with the JSON schema, `tool_choice` forced). No loop, no QGIS access.
- The UI shows the resulting config as a human-readable summary ("Seasons 2019, 2021, 2022 (rain above mean Oct–Mar), 4 zones") with Confirm / Edit; only then is a job created.
- Server-side validation with the same schema; invalid output is rejected, never executed.
- Rate-limit per user; log prompts/outputs without storing field data.

If this turns out unnecessary, ship the form only; nothing else depends on it.

---

## 11. Security notes

- Never run user- or model-supplied code. The only code path is the pipeline reading a validated config.
- Upload validation: extension + magic bytes, size limits, GDAL/OGR open test in the worker with a timeout; reject zips with path traversal; cap raster dimensions and vector feature count.
- Worker runs as non-root, read-only root FS where possible, CPU/memory limits, per-job scratch dir deleted in `finally`.
- Service role key only in the worker and server routes; browser uses anon key + RLS. Signed URLs for downloads (short TTL).
- Egress allow-list for the worker: Supabase, Earth Search STAC + its S3 host, Open-Meteo.
- Data deletion: removing a project cascades to DB rows and Storage objects (background cleanup job).
- Privacy: farm data is sensitive; state in the ToS that data is used only to produce outputs.

---

## 12. MVP cut

**In:** auth, project/field, upload of yield CSV/SHP + one boundary, Sentinel-2 NDVI, season weather filter by rainfall, k-means zones (3–5), shapefile + GPKG + PNG download, live job progress.
**Out (v1.1+):** drone orthomosaic NDVI/VARI, chat layer, multiple crops per rotation, kriging, team sharing, billing.

---

## 13. Agent prompts (5 build prompts)

Use **Prompt 0 once** as `AGENTS.md` (persistent context), then paste prompts 1–5 in order. Each prompt is intentionally large and self-contained, with acceptance criteria the agent must run before finishing. Prompts 1 and 2 can run in parallel in separate branches (they only meet at the job contract in `pipeline/config.py`, which Prompt 2 owns).

### Prompt 0 — Master context (save as AGENTS.md)

````text
You are building "Field Productivity Zones": a Next.js (App Router, TypeScript) + Supabase web app plus a Python worker that turns uploaded field data (yield maps, drone orthomosaics, Sentinel-2) into productivity-zone maps and a downloadable shapefile.

Non-negotiable rules:
1. There is NO AI/agent in the processing path. The pipeline is a deterministic Python package (services/worker/pipeline) driven by a pydantic-validated JSON config. Never execute user-supplied or model-supplied code.
2. pipeline/ never imports Supabase. worker/ is a thin adapter: claim job -> download -> pipeline.run(config, workdir) -> upload -> update job.
3. Queue = Postgres `jobs` table with FOR UPDATE SKIP LOCKED. No Redis.
4. RLS on every table; owner chain via projects.owner_id. Service role key only server-side/worker.
5. QGIS headless (QT_QPA_PLATFORM=offscreen, one qgis_session per job process) only for: IDW interpolation, polygonize/smoothing, styled map rendering, .qgz. Everything else uses rasterio/geopandas/scikit-learn.
6. Shapefile field names <= 10 chars. Output CRS = local UTM chosen from field centroid.
7. Every module has tests. Use a synthetic field generator (tests/synthetic.py) so tests need no real data and no network (mock Open-Meteo and STAC with recorded fixtures).
8. Keep docs/DECISIONS.md updated with every non-obvious choice (image tag, GDAL/pip conflicts, library versions, API licences).
9. Work autonomously: do not stop to ask questions; make a reasonable decision, document it, continue. Before finishing, run all tests/linters/type checks and fix failures.
10. Commit in small logical commits with clear messages.

Reference: PROJECT_PLAN.md sections 5 (data model), 6 (job contract), 7 (pipeline), 8 (weather filter), 9 (Docker).
````

### Prompt 1 — Foundation: web app, database, storage, auth (run in parallel with Prompt 2)

````text
Read AGENTS.md and PROJECT_PLAN.md. Build the whole foundation in one pass.

Deliver:
A. Monorepo scaffold: apps/web (Next.js App Router, TS strict, Tailwind, shadcn/ui, ESLint, Prettier), supabase/ (CLI project), docs/, docker-compose.yml (local Supabase via CLI + worker service stub), .github/workflows/ci.yml (lint, typecheck, web tests, pytest, migration check), .env.example files, Makefile with `make dev|test|lint|db-reset`.
B. Supabase migrations implementing PROJECT_PLAN section 5 exactly (projects, fields, datasets, jobs, job_outputs, zones, enums, indexes) plus: RLS policies on every table, storage buckets (`uploads`, `outputs`, both private) with policies keyed on the first path segment = owner_id, a `claim_job()` SQL function using SKIP LOCKED, a `reap_stale_jobs()` function, and Realtime enabled on `jobs`. Add pgTAP or SQL tests that prove a second user cannot read/write the first user's rows or objects.
C. Auth: Supabase email + magic link, middleware-protected routes, server/client Supabase helpers, typed DB types generated from the schema.
D. Pages and API routes: dashboard (projects list/create), project page (fields list/create, boundary upload or draw), field page with tabs: Data, Analyses, Results (Analyses/Results can be placeholders that read the jobs table).
E. Uploads: resumable uploads (tus via Supabase Storage) with progress, pause/resume, type detection by extension and magic bytes (GeoTIFF, zipped Shapefile, GeoJSON, CSV, GPKG), size limits per kind, and a metadata form per dataset (kind, season_year, crop, yield column mapping for CSV/SHP, units). Writes a `datasets` row on completion. Boundary uploads are converted to a MultiPolygon in `fields.boundary` (do this in a server route with a small validated parser; if it needs GDAL, create a `queued` job of kind 'ingest_boundary' instead and note it for the worker).
F. Analysis form: build a typed form (react-hook-form + zod) that produces the exact config JSON in PROJECT_PLAN section 6 (zone count/auto, season weather filter with metric/window/rule, layers, yield options, exports). Mirror the pydantic schema as a zod schema in packages/shared and add a script that fails CI if the two drift (export JSON Schema from pydantic once Prompt 2 lands; until then use a checked-in schema file). Submitting inserts a `jobs` row with status 'queued'.
G. Job UI: subscribe to Realtime for the job row; show step, progress bar, log tail, error, cancel button (sets status 'canceled'); Results page lists job_outputs with signed download URLs and renders the zones from the `zones` table on a MapLibre map (field boundary + zones colored by yield class + legend + per-zone stats table).
H. Tests: Vitest for utilities and zod schema, Playwright smoke test (sign in, create project/field, upload a small CSV, create analysis job row). docs/RUNBOOK.md section for local setup.

Acceptance: `make db-reset && make test` passes; a fresh clone can run the app locally against local Supabase; RLS tests pass; a job row created from the UI shows up as 'queued' and updates live when changed manually in SQL.
````

### Prompt 2 — Geo pipeline + Docker image (run in parallel with Prompt 1)

````text
Read AGENTS.md and PROJECT_PLAN.md. Build the entire processing pipeline and Docker image in one pass. No Supabase code in pipeline/.

Deliver:
A. Docker: services/worker/Dockerfile per PROJECT_PLAN section 9. Pick and PIN a qgis/qgis tag; verify `python -c "from qgis.core import Qgis; import rasterio, geopandas, sklearn"` works inside the container; resolve any pip-vs-system GDAL/numpy conflicts with constraints.txt; record the tag, versions and any conflict resolutions in docs/DECISIONS.md. Add a `make worker-test` that runs pytest inside the container.
B. pipeline/qgis_runtime.py (qgis_session context manager, run_algorithm helper with error capture and logging).
C. pipeline/config.py: pydantic v2 models for the full job contract (section 6), extra="forbid", sensible defaults, validators (k_min<=k_max, window dates, units). Add `python -m pipeline.cli schema` that prints JSON Schema and write it to packages/shared/config.schema.json.
D. Modules, each with docstrings, type hints, progress callbacks `report(step, pct, msg)` and unit tests:
   - ingest.py: detect vector/raster, read CRS (fail clearly if missing), reproject to local UTM, clip to boundary, write COG (with overviews) / GPKG.
   - yield_clean.py: column mapping, unit conversion, drop zero/null, sigma + IQR outlier removal, optional speed/swath/lag filters when columns exist, report how many points were removed and why.
   - interpolate.py: IDW via QGIS processing to resolution_m, masked by boundary; fallback to scipy-based IDW if QGIS fails (log it).
   - ndvi.py + sentinel.py: Sentinel-2 L2A via pystac-client on Earth Search, windowed reads with rasterio over the field bbox, SCL cloud masking, median composite over the season window, resample to the analysis grid. Drone NDVI from NIR/Red bands, VARI fallback for RGB with a report flag.
   - weather.py + crop_calendars.yaml: Open-Meteo daily fetch with on-disk cache, window handling across new year, metrics (precip_sum_mm, gdd_base10, tmax_mean, heat_days_gt35, dry_spell_max), rules (above_mean, below_mean, between, top_n, bottom_n), decision table with reasons, min_seasons failure.
   - normalize.py: per-season z-score or % of mean; stack and stability (mean, CV).
   - zones.py: scale features, k-means (fixed k or auto by silhouette), order by yield, majority filter, polygonize, dissolve, merge sub-min-area polygons, per-zone stats.
   - export.py: zipped shapefile (<=10-char fields, .prj, .cpg), GPKG, zones GeoTIFF, preview.png and .qgz via PyQGIS layout/renderer with a legend, report.json (includes season decision table, step timings, removal counts), params.json.
E. pipeline/run.py: orchestrator `run(config, inputs, workdir, report) -> Outputs`, fixed step order from section 7, cancellation check between steps, clean exceptions (PipelineError with user-readable message).
F. pipeline/cli.py: `python -m pipeline.cli run config.json --inputs inputs.json --workdir ./out`, `schema`, `synth` (generates a synthetic field with N seasons: boundary, yield points with spatial trend + noise + outliers, a fake NDVI raster, so the whole pipeline runs offline).
G. tests/: synthetic generator; unit tests per module; one end-to-end test that runs the CLI on synthetic data with mocked weather/STAC and asserts: shapefile opens in geopandas, field names <=10 chars, k zones within [k_min,k_max], zones cover the boundary with no gaps >1%, no polygon < min_area_ha, excluded seasons match the weather rule, report.json valid.

Acceptance: `make worker-test` passes inside Docker; `python -m pipeline.cli synth && python -m pipeline.cli run ...` produces all outputs; docs/DECISIONS.md lists the pinned image and every conflict resolved.
````

### Prompt 3 — Worker, integration and real-data hardening of the pipeline

````text
Read AGENTS.md and PROJECT_PLAN.md. Prompts 1 and 2 are merged. Build the worker and integrate everything end to end.

Deliver:
A. services/worker/worker/: main.py (claim loop via claim_job(), heartbeat thread every 30s, graceful SIGTERM, one job at a time, per-job tempdir removed in finally), db.py (psycopg pool, job update helpers, progress throttling to <=2 writes/sec), storage.py (download inputs/upload outputs with retries and checksums, using service role), job handlers: 'analysis' (runs pipeline.run), 'ingest_boundary' (boundary -> fields.boundary), 'ingest_dataset' (validate with GDAL/OGR, extract metadata: CRS, bbox, bands, feature count, columns into datasets.meta; reject bad files with readable errors).
B. Result persistence: insert job_outputs, insert `zones` rows (geometry reprojected to 4326) so the web app can render them, write report summary into jobs.log, set final status. Idempotent: re-running a job id replaces its outputs/zones.
C. Failure handling: PipelineError -> status failed with user-readable `error`; unexpected exceptions -> failed with a short error + full traceback in logs only; QGIS segfault protection by running the pipeline in a subprocess with timeout (default 30 min, configurable) and memory cap; cancellation: poll jobs.status between steps; retry policy via reap_stale_jobs (max 2).
D. Integration environment: docker-compose with local Supabase + worker; `make e2e` that seeds a user, uploads synthetic datasets through the Storage API, inserts an analysis job, waits for success, downloads the shapefile and validates it with geopandas.
E. Real-data hardening: add fixtures or documented sample-data scripts for (1) a CSV yield export with common column names (John Deere, Case IH, Trimble-style, generic lat/lon/yield), (2) a zipped yield shapefile in a geographic CRS, (3) a multi-band GeoTIFF with NIR. Make ingest/yield_clean auto-suggest column mappings (fuzzy name matching) and expose it as `datasets.meta.suggested_mapping`. Handle: missing CRS prompt (job fails with actionable message asking the user to set the CRS in the dataset form), mixed units, seasons crossing the new year, fields in the southern hemisphere, fields spanning two UTM zones (choose by centroid).
F. Observability: structured JSON logging with job_id, step timings table in report.json, health endpoint (HTTP :8080/healthz) for the host, optional Sentry hook via env var.
G. Update the web form to show `suggested_mapping` and let the user confirm it (small change in apps/web).

Acceptance: `make e2e` passes from a clean checkout; killing the worker mid-job causes the job to be re-claimed and finished by a restarted worker; a corrupted upload yields a clear user-facing error; two jobs enqueued simultaneously are processed serially without interference.
````

### Prompt 4 — Product polish: results UX, season/weather transparency, optional chat translator

````text
Read AGENTS.md and PROJECT_PLAN.md. The pipeline and worker work end to end. Build the product layer in one pass.

Deliver:
A. Season selection UX: before running, a "Preview seasons" action that calls a lightweight server route (or a 'weather_preview' job kind if it needs Python) returning per-season metric values vs the reference mean, so the user sees which seasons the rule would include. Show a chart (Recharts) of the metric per year with included/excluded colors and the mean line; allow manual override toggles per season; overrides are written into config as `seasons.include: [years]`.
B. Results page v2: map with toggleable layers (zones, yield-mean raster preview, NDVI mean preview, field boundary, optional orthomosaic COG tile via a signed-URL tile proxy), zone stats table (area, yield mean, CV, NDVI, % of field), season decision table from report.json, download buttons (shp.zip, gpkg, tif, png, qgz, report.json) with signed URLs, and "Re-run with changes" that clones the config into a new analysis.
C. Analyses history: list per field with status, config summary text (human-readable renderer for the config), duration, compare two runs side by side (swipe or split map).
D. Optional chat translator (single call, no agent loop, no QGIS access): Next.js route /api/chat-to-config using the Claude API with a forced single tool `emit_config` whose input schema is packages/shared/config.schema.json. Inputs: user message + field metadata (seasons available, crops, datasets). Output: config JSON + a plain-language summary. Validate server-side with zod; on failure return a friendly error and never create a job. UI: chat box in the Analyses tab that fills the form and shows the summary with Confirm/Edit; it must never submit a job without explicit confirmation. Rate limit (per-user, per-day), no field data in the prompt beyond metadata, model name in an env var. Add tests with a mocked API client including adversarial inputs (prompt injection in dataset names, absurd values) that must be rejected by validation.
E. Empty/error states, loading skeletons, mobile-friendly layout, accessibility pass (keyboard nav, contrast), i18n scaffolding with English + Portuguese + Spanish for UI strings (units: ha, kg/ha, bu/ac configurable).
F. Playwright e2e for: upload -> preview seasons -> run -> view results -> download shapefile; chat translator happy path and rejection path with the mocked API.

Acceptance: all web tests and Playwright pass; a user can do the full flow with the form alone; the chat translator can be disabled via an env flag and nothing else breaks.
````

### Prompt 5 — Hardening, security, deployment and docs

````text
Read AGENTS.md and PROJECT_PLAN.md. Everything works locally. Make it production-ready in one pass.

Deliver:
A. Security review and fixes against PROJECT_PLAN section 11: upload validation (magic bytes, zip-slip protection, decompression-bomb limits, raster dimension and feature count caps), strict config validation limits (max seasons, max resolution bounds, max area), signed URL TTLs, CORS and CSP headers, rate limiting on API routes, secrets handling, dependency audit (npm audit, pip-audit) in CI, worker container hardening (non-root, read-only FS where possible, resource limits, seccomp default), egress allow-list documentation. Add an RLS regression test suite that runs in CI.
B. Quotas and cost control: per-user limits (storage GB, jobs/day, max field area), enforced both in DB (check constraints/triggers or RPC) and UI messages.
C. Data lifecycle: background cleanup of orphaned storage objects, retention policy for job outputs (configurable days), project deletion that cascades DB + Storage, export-my-data endpoint.
D. Deployment: Vercel config for apps/web; worker deployment guide + config for one concrete host (Fly.io or Hetzner with Docker Compose + systemd), including secrets, healthchecks, autoscaling-by-replicas instructions, a GitHub Action that builds, scans (Trivy) and pushes the worker image, DB migration workflow (staging -> production), backup/restore notes for Supabase.
E. Observability: dashboards/alerts documentation (job failure rate, queue depth, p95 duration), a `/admin/jobs` page (role-gated) with queue depth and recent failures, structured logs.
F. Performance pass: profile the pipeline on a synthetic 200 ha field with 5 seasons and a 20 GB orthomosaic-sized raster (windowed/overview-based processing only); record timings and memory in docs/PERFORMANCE.md; fix the worst bottleneck.
G. Documentation: README (what it is, architecture diagram, quick start), docs/RUNBOOK.md (deploy, rollback, stuck jobs, reaper, upgrading the QGIS image tag), docs/DATA_FORMATS.md (supported inputs, column mapping rules), docs/CONFIG.md (every config field with examples), docs/DECISIONS.md final review, CONTRIBUTING.md.
H. Final verification script `make release-check` running lint, typecheck, unit, RLS, e2e, container scan and printing a pass/fail table.

Acceptance: `make release-check` passes; a deployment dry run to staging succeeds following RUNBOOK.md only; the security checklist in docs/SECURITY.md has every item marked done or explicitly deferred with a reason.
````

**Why 5 prompts instead of 12:** each prompt maps to one coherent, testable slice with a clear boundary (web/DB, pipeline, worker integration, product layer, hardening). Merging further would exceed what one agent session can reliably verify. If the agent runs out of context mid-prompt, split along the lettered sections (A–H); they are ordered so each lettered block is independently shippable.

---

## 14. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| pip vs system GDAL/numpy conflicts in the image | Build breaks or silent wrong results | `constraints.txt`, venv with system site packages, import smoke test in CI (Prompt 2) |
| QGIS crashes in headless mode | Lost jobs | Run pipeline in subprocess with timeout; fallback scipy IDW |
| Messy yield data (units, columns, lag, no CRS) | Bad zones | Fuzzy column mapping, explicit confirmation UI, removal report, fail with actionable errors |
| Cloudy Sentinel-2 seasons | Missing NDVI | SCL mask, median composite, widen window, report coverage %; zones can run on yield-only |
| Few seasons after weather filter | Unstable zones | `min_seasons`, decision table with option to relax rule |
| Open-Meteo licence for commercial use | Legal | Check terms in Phase 2; swap provider behind `weather.py` interface (NASA POWER, ERA5) |
| Large orthomosaics | Memory/time | COG + overviews, windowed processing, analysis at `resolution_m` |
| Worker hosting cost | Budget | Scale-to-zero worker with a wake-up on new job, or a single small VPS |
| Zones differ visibly between runs | Trust | Fixed random seed, record seed in `params.json` |
| Yield-based zones only valid for that crop/rotation | Misinterpretation | Normalize per season, show n_seasons and CV per zone, document limits in UI |

---

## 15. Open decisions (settle during Prompt 2)

1. Pin the tested `qgis/qgis` tag and record it in `docs/DECISIONS.md`.
2. Confirm which pip packages must be apt-installed to avoid GDAL conflicts.
3. Decide IDW only vs IDW + optional kriging for v1.1.
4. Confirm Open-Meteo licence fit; keep provider swappable.
5. Choose worker host (Fly.io vs Hetzner VPS) before Prompt 5.
