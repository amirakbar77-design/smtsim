.PHONY: install install-service test test-service lint run stats compare serve demo demo-api clean

install:
	uv sync --extra yaml

install-service:
	uv sync --extra yaml --extra service

lint:
	uv run ruff check src/ tests/

test:
	uv run pytest

# Needs a Postgres; see .env.example. Without SMTSIM_TEST_DATABASE_URL the
# service tests skip and only the simulation suite runs.
test-service:
	uv run pytest -q

run:
	uv run smtsim run --minutes 480 --seed 42 --out runs/run1.jsonl

stats:
	uv run smtsim stats runs/run1.jsonl

compare:
	uv run smtsim compare configs/baseline.toml configs/two_placers.toml \
		--seeds 30 --minutes 480 --warmup 30 --out runs/comparison.json

serve:
	docker compose up -d --wait
	@echo "API on http://localhost:8000/docs"

demo:
	./scripts/record-demo.sh

demo-api:
	./scripts/record-demo.sh api

clean:
	rm -rf runs/*.jsonl runs/*.json .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
