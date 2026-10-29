# Field Productivity Zones Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Next.js + Supabase web app and a deterministic Python/QGIS worker that turns yield maps and Sentinel-2 into productivity-zone shapefiles.

**Architecture:** Browser uploads to private Supabase Storage and inserts a validated analysis config into `jobs`. A Docker worker claims rows with `FOR UPDATE SKIP LOCKED`, downloads inputs, runs pure `pipeline.run(config, workdir)`, uploads outputs, and writes `zones` for MapLibre. No AI in the processing path; no Redis.

**Tech Stack:** Next.js App Router (TS strict, Tailwind), Supabase (Auth, Postgres/PostGIS, Storage, Realtime), Python 3 + pydantic v2 + rasterio/geopandas/sklearn, optional PyQGIS headless for IDW/render, Zod shared schema, Docker `qgis/qgis` pinned tag.

**Spec:** `PROJECT_PLAN.md` (v2 scripted pipeline) + `AGENTS.md`

## Global Constraints

- `pipeline/` never imports Supabase; worker is thin adapter only.
- Config: pydantic v2 + zod, `extra="forbid"`, single JSON Schema in `packages/shared/config.schema.json`.
- Queue = `jobs` + `claim_job()` SKIP LOCKED; reaper resets stale heartbeats.
- RLS on every table via `projects.owner_id`; service role only server/worker.
- QGIS only for IDW, polygonize/smooth, preview PNG, `.qgz`; else rasterio/geopandas/sklearn.
- Shapefile fields ≤10 chars; CRS = local UTM from field centroid.
- Tests offline via `tests/synthetic.py`; mock Open-Meteo and STAC.
- Record non-obvious choices in `docs/DECISIONS.md`.
- MVP in: auth, project/field, yield CSV/SHP + boundary, Sentinel NDVI, weather filter, k-means 3–5, shp/gpkg/png, live job progress. Out: drone NDVI, chat layer, billing.

## Review Focus

1. Second user cannot read first user's projects/datasets/jobs/storage — RLS + storage path = `owner_id/...`.
2. Invalid or drifted config rejected before any geo step — pydantic + zod + schema drift CI.
3. Weather filter with < `min_seasons` fails fast with decision table, never silent empty stack.
4. Missing CRS on upload fails with actionable message, never silent WGS84 assume.
5. QGIS unavailable: scipy IDW fallback still produces valid zones in unit tests.

---

### Task 1: Repo scaffold and shared contract

**Files:**
- Create: `Makefile`, `docker-compose.yml`, `.gitignore`, `.env.example`, `.github/workflows/ci.yml`
- Create: `packages/shared/package.json`, `packages/shared/src/config.ts`, `packages/shared/config.schema.json`
- Create: `docs/DECISIONS.md`, `docs/RUNBOOK.md`
- Modify: `README.md`

**Interfaces:**
- Produces: Zod `AnalysisConfigSchema` matching PROJECT_PLAN §6; JSON Schema file checked into git.
- Produces: `make test|lint|db-reset|worker-test` targets (stubs OK until later tasks).

- [ ] **Step 1:** Write `packages/shared` zod schema for full job config (zones, layers, seasons/weather, yield, resolution_m, export).
- [ ] **Step 2:** Write checked-in `config.schema.json` mirror; document drift-check script intent in Makefile.
- [ ] **Step 3:** Root Makefile, gitignore, env example, CI workflow (lint/typecheck/test placeholders).
- [ ] **Step 4:** Commit `chore: monorepo scaffold and shared config schema`

---

### Task 2: Supabase schema, RLS, claim/reap

**Files:**
- Create: `supabase/config.toml`
- Create: `supabase/migrations/20261028000000_init.sql`
- Create: `supabase/tests/rls.test.sql` (or pgTAP if available)

**Interfaces:**
- Produces tables: `projects`, `fields`, `datasets`, `jobs`, `job_outputs`, `zones` + enums from §5.
- Produces: `claim_job(worker_id text)`, `reap_stale_jobs(max_age interval default '2 minutes', max_retries int default 2)`.
- Produces: storage buckets `uploads`, `outputs` private; path first segment = owner_id.
- Produces: Realtime publication on `jobs`.

- [ ] **Step 1:** Migration with PostGIS, tables, indexes, RLS policies, claim/reap functions, buckets.
- [ ] **Step 2:** SQL test proving user B cannot select user A rows.
- [ ] **Step 3:** Commit `feat(db): init schema, RLS, claim_job, reap_stale_jobs`

---

### Task 3: Pipeline config + CLI + synthetic generator

**Files:**
- Create: `services/worker/pyproject.toml` or `requirements.txt` + `constraints.txt`
- Create: `services/worker/pipeline/__init__.py`, `config.py`, `cli.py`, `errors.py`
- Create: `services/worker/tests/synthetic.py`, `tests/test_config.py`
- Create: `services/worker/pipeline/crop_calendars.yaml`

**Interfaces:**
- Produces: `AnalysisConfig` pydantic model; `PipelineError(message: str, details: dict | None)`
- Produces: `python -m pipeline.cli schema|synth|run`
- Produces: `synth(workdir, n_seasons=3) -> inputs dict` offline fixtures

- [ ] **Step 1:** Failing tests for config validation (k_min≤k_max, extra forbid, window dates).
- [ ] **Step 2:** Implement pydantic models + CLI schema export to `packages/shared/config.schema.json`.
- [ ] **Step 3:** Synthetic field generator (boundary polygon, yield points with trend+noise+outliers, fake NDVI grid).
- [ ] **Step 4:** Commit `feat(pipeline): config models, CLI, synthetic generator`

---

### Task 4: Pipeline modules (ingest → export)

**Files:**
- Create: `pipeline/qgis_runtime.py`, `ingest.py`, `yield_clean.py`, `interpolate.py`, `ndvi.py`, `sentinel.py`, `weather.py`, `normalize.py`, `zones.py`, `export.py`, `run.py`
- Create: matching `tests/test_*.py` per module + `tests/test_e2e_pipeline.py`
- Create: `tests/fixtures/` recorded Open-Meteo + STAC responses

**Interfaces:**
- Consumes: `AnalysisConfig`, synthetic inputs
- Produces: `run(config, inputs, workdir, report, is_canceled=None) -> Outputs`
- Produces: `Outputs` with paths: shp_zip, gpkg, zones_tif, preview_png, qgz?, report_json, params_json
- Progress: `report(step: str, pct: int, msg: str) -> None`

- [ ] **Step 1:** Unit tests per module using synthetic data (QGIS optional; scipy IDW fallback).
- [ ] **Step 2:** Implement modules in §7 order; weather metrics/rules from §8.
- [ ] **Step 3:** E2E test: CLI run on synth → shapefile opens, fields ≤10 chars, k in range, min area, report valid.
- [ ] **Step 4:** Commit `feat(pipeline): full deterministic geo pipeline`

---

### Task 5: Worker Docker + adapter stub

**Files:**
- Create: `services/worker/Dockerfile`, `worker/main.py`, `worker/db.py`, `worker/storage.py`
- Modify: `docker-compose.yml` worker service
- Create: `docs/DECISIONS.md` pinned image tag entry

**Interfaces:**
- Produces: container CMD `python -m worker.main`
- Produces: claim loop, heartbeat 30s, tempdir finally, handlers analysis (pipeline only in this task; ingest_* can stub)

- [ ] **Step 1:** Pin `qgis/qgis` tag; constraints forbid reinstalling gdal/numpy; record in DECISIONS.
- [ ] **Step 2:** Worker claim/heartbeat/progress update using service role.
- [ ] **Step 3:** `make worker-test` runs pytest in image (or host fallback documented if Docker unavailable).
- [ ] **Step 4:** Commit `feat(worker): Docker image and claim loop adapter`

---

### Task 6: Next.js web foundation

**Files:**
- Create: `apps/web/` Next.js App Router app (TS, Tailwind, ESLint)
- Create: Supabase clients, middleware auth, typed DB helpers
- Create: pages — dashboard, project, field (Data / Analyses / Results tabs)
- Create: upload flow, analysis form (RHF + zod from shared), job realtime UI, MapLibre results stub
- Create: `apps/web` vitest tests for schema/utils

**Interfaces:**
- Consumes: `AnalysisConfigSchema` from `@fpz/shared`
- Produces: insert `jobs` row status `queued` on form submit
- Produces: Realtime subscription on job row (step, progress, log, error, cancel)

- [ ] **Step 1:** Scaffold Next app with auth (email magic link), protected routes.
- [ ] **Step 2:** CRUD projects/fields; dataset upload metadata form; boundary GeoJSON path.
- [ ] **Step 3:** Analysis form → jobs insert; job progress UI; results list + map placeholder.
- [ ] **Step 4:** Vitest for zod config; commit `feat(web): auth, projects, analysis form, job UI`

---

### Task 7: Integration glue and verification

**Files:**
- Modify: Makefile targets wired to real commands
- Create/update: `docs/RUNBOOK.md` local setup
- Modify: CI workflow full lint/test

- [ ] **Step 1:** `make test` runs web vitest + worker pytest on host.
- [ ] **Step 2:** `make lint` eslint + ruff + tsc.
- [ ] **Step 3:** Fix failures; update DECISIONS for any new choices.
- [ ] **Step 4:** Commit `chore: wire make targets and docs`

---

## Execution notes

- Prefer host pytest without QGIS (scipy IDW) for fast CI; Docker image tests when Docker available.
- Pin QGIS tag on first successful local pull; if pull fails, document fallback tag attempt order in DECISIONS.
- Prompts 3–5 (full e2e, polish, hardening) are follow-on plans after Tasks 1–7 land.
