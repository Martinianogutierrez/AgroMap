.PHONY: test lint db-reset worker-test schema-check install e2e samples

PY ?= python

install:
	cd packages/shared && npm install
	cd apps/web && npm install --legacy-peer-deps
	cd services/worker && $(PY) -m pip install -r requirements.txt

test:
	cd packages/shared && npm test --if-present
	cd apps/web && npm test --if-present
	cd services/worker && $(PY) -m pytest tests -q

lint:
	cd apps/web && npx tsc --noEmit && npx eslint . --max-warnings 0
	cd services/worker && $(PY) -m ruff check pipeline worker tests scripts

db-reset:
	npx supabase db reset --yes

worker-test:
	docker build -t fpz-worker:local services/worker
	docker run --rm fpz-worker:local python -m pytest tests -q

schema-check:
	cd services/worker && $(PY) -m pipeline.cli schema > ../../packages/shared/config.schema.generated.json
	diff -u packages/shared/config.schema.json packages/shared/config.schema.generated.json

samples:
	cd services/worker && $(PY) scripts/make_sample_data.py

# Offline e2e always. With DATABASE_URL + SUPABASE_SERVICE_ROLE_KEY runs Supabase e2e too.
e2e:
	cd services/worker && $(PY) scripts/e2e_run.py
