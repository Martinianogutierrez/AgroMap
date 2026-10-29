# Field Productivity Zones (AgroMap)

Next.js + Supabase web app and a deterministic Python worker that turn yield maps and Sentinel-2 into productivity-zone shapefiles.

## Architecture

```
Browser → Supabase Storage + jobs table
                ↓
        Worker (Docker / QGIS optional)
                ↓
        pipeline.run(config) → outputs + zones
```

No AI in the processing path. Queue = Postgres `FOR UPDATE SKIP LOCKED`.

## Quick start

```bash
cp .env.example .env
npx supabase start
# copy URL + keys into .env

cd packages/shared && npm i && npm test
cd ../../apps/web && npm i --legacy-peer-deps && npm test
cd ../../services/worker
# Python 3.11+
python -m pip install -r requirements.txt
python -m pytest tests -q

cd ../..
# web
cd apps/web && npm run dev
```

Offline pipeline smoke:

```bash
cd services/worker
python -m pipeline.cli synth --workdir ./scratch/synth
# then run with fixtures from tests (see docs/RUNBOOK.md)
python -m pytest tests/test_e2e_pipeline.py -q
```

## Docs

- `PROJECT_PLAN.md` — full design
- `AGENTS.md` — agent rules
- `docs/DECISIONS.md` — pins and tradeoffs
- `docs/RUNBOOK.md` — local ops
- `docs/superpowers/plans/2026-10-28-field-productivity-zones.md` — implementation plan

## Status (MVP scaffold)

| Slice | State |
|-------|--------|
| Shared zod/pydantic config | done |
| Supabase migration + RLS + claim/reap | done |
| Pipeline (scipy IDW, zones, export) + tests | done (13 pytest) |
| Worker adapter + Dockerfile | stub/MVP |
| Next.js auth, projects, fields, form, jobs UI | done |
| Dataset→storage resolution in worker | Prompt 3 |
| Live STAC/Open-Meteo in prod worker | Prompt 3 |
| Chat translator | Prompt 4 (out of MVP) |
