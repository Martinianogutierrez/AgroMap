# Runbook — local development

## Prerequisites

- Node 20+, npm
- Python 3.11+ (host tests; QGIS optional)
- Docker (worker image + optional full stack)
- Supabase CLI (`npx supabase` works)

## First setup

```bash
cp .env.example .env
# fill NEXT_PUBLIC_SUPABASE_URL, anon key, service role (from `supabase start`)

make db-reset          # applies migrations
cd apps/web && npm i && cd ../..
cd packages/shared && npm i && cd ../..
cd services/worker && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt && cd ../..

make test
make lint
```

## Dev loop

```bash
# terminal 1 — Supabase
npx supabase start

# terminal 2 — Next.js
cd apps/web && npm run dev

# terminal 3 — worker (needs DATABASE_URL + service role)
cd services/worker && python -m worker.main
```

## Useful make targets

| Target | Meaning |
|--------|---------|
| `make test` | web vitest + worker pytest (host, scipy IDW) |
| `make worker-test` | pytest inside Docker image |
| `make lint` | eslint + tsc + ruff |
| `make db-reset` | `supabase db reset` |

## Stuck jobs

```sql
select id, status, step, heartbeat_at, worker_id from jobs order by created_at desc limit 20;
select * from reap_stale_jobs();  -- resets running with old heartbeat
```

## Pipeline offline smoke

```bash
cd services/worker
python -m pipeline.cli synth --workdir /tmp/fpz-synth
python -m pipeline.cli run /tmp/fpz-synth/config.json --inputs /tmp/fpz-synth/inputs.json --workdir /tmp/fpz-synth/out
```
