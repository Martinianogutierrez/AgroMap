# Field Productivity Zones — Agent Context

You are building "Field Productivity Zones": a Next.js (App Router, TypeScript) + Supabase web app plus a Python worker that turns uploaded field data (yield maps, drone orthomosaics, Sentinel-2) into productivity-zone maps and a downloadable shapefile.

## Non-negotiable rules

1. There is NO AI/agent in the processing path. The pipeline is a deterministic Python package (`services/worker/pipeline`) driven by a pydantic-validated JSON config. Never execute user-supplied or model-supplied code.
2. `pipeline/` never imports Supabase. `worker/` is a thin adapter: claim job -> download -> `pipeline.run(config, workdir)` -> upload -> update job.
3. Queue = Postgres `jobs` table with `FOR UPDATE SKIP LOCKED`. No Redis.
4. RLS on every table; owner chain via `projects.owner_id`. Service role key only server-side/worker.
5. QGIS headless (`QT_QPA_PLATFORM=offscreen`, one `qgis_session` per job process) only for: IDW interpolation, polygonize/smoothing, styled map rendering, `.qgz`. Everything else uses rasterio/geopandas/scikit-learn.
6. Shapefile field names <= 10 chars. Output CRS = local UTM chosen from field centroid.
7. Every module has tests. Use a synthetic field generator (`tests/synthetic.py`) so tests need no real data and no network (mock Open-Meteo and STAC with recorded fixtures).
8. Keep `docs/DECISIONS.md` updated with every non-obvious choice (image tag, GDAL/pip conflicts, library versions, API licences).
9. Work autonomously: do not stop to ask questions; make a reasonable decision, document it, continue. Before finishing, run all tests/linters/type checks and fix failures.
10. Commit in small logical commits with clear messages.

## Reference

`PROJECT_PLAN.md` sections 5 (data model), 6 (job contract), 7 (pipeline), 8 (weather filter), 9 (Docker).

## Repo layout

```
/apps/web                 Next.js (App Router, TypeScript)
/services/worker
   Dockerfile
   constraints.txt
   worker/                thin adapter (Supabase OK)
   pipeline/              pure Python, no Supabase
   tests/
/supabase/migrations
/packages/shared          zod + config.schema.json
/docs
```

## Local commands

- `make test` — web vitest + worker pytest (host, no QGIS required for unit tests with scipy fallback)
- `make worker-test` — pytest inside Docker image
- `make lint` — eslint + ruff + tsc
- `make db-reset` — supabase db reset
