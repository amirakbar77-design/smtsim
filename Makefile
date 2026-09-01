.PHONY: install test run stats compare demo clean

install:
	uv sync --extra yaml

test:
	uv run pytest

run:
	uv run smtsim run --minutes 480 --seed 42 --out runs/run1.jsonl

stats:
	uv run smtsim stats runs/run1.jsonl

compare:
	uv run smtsim compare configs/baseline.toml configs/two_placers.toml \
		--seeds 30 --minutes 480 --warmup 30 --out runs/comparison.json

demo:
	./scripts/record-demo.sh

clean:
	rm -rf runs/*.jsonl runs/*.json .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
