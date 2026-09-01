.PHONY: install test run stats demo clean

install:
	uv sync --extra yaml

test:
	uv run pytest

run:
	uv run smtsim run --minutes 480 --seed 42 --out runs/run1.jsonl

stats:
	uv run smtsim stats runs/run1.jsonl

demo:
	./scripts/record-demo.sh

clean:
	rm -rf runs/*.jsonl .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
