.PHONY: test lint db-reset worker-test schema-check install

install:
	cd packages/shared && npm install
	cd apps/web && npm install
	cd services/worker && python -m pip install -r requirements.txt -c constraints.txt

test:
	cd packages/shared && npm test --if-present
	cd apps/web && npm test --if-present
	cd services/worker && python -m pytest tests -q

lint:
	cd apps/web && npx tsc --noEmit && npx eslint . --max-warnings 0
	cd services/worker && python -m ruff check pipeline worker tests

db-reset:
	npx supabase db reset --yes

worker-test:
	docker build -t fpz-worker:local services/worker
	docker run --rm fpz-worker:local python -m pytest tests -q

schema-check:
	cd services/worker && python -m pipeline.cli schema > ../../packages/shared/config.schema.generated.json
	diff -u packages/shared/config.schema.json packages/shared/config.schema.generated.json
