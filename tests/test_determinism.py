"""The same seed must reproduce a run exactly, byte for byte."""

from __future__ import annotations

import hashlib
import io
import json
import random
from pathlib import Path

from smtsim.config import DEFAULT_LINE, LogNormal, Triangular
from smtsim.events import JsonlSink, ListSink, open_jsonl
from smtsim.line import simulate

from conftest import SHIFT_SECONDS, run_events


def write_log(path: Path, seed: int, minutes: float = 480.0) -> bytes:
    with open_jsonl(path) as sink:
        simulate(minutes * 60.0, sink=sink, seed=seed)
    return path.read_bytes()


def test_same_seed_produces_byte_identical_log(tmp_path: Path) -> None:
    first = write_log(tmp_path / "first.jsonl", seed=42)
    second = write_log(tmp_path / "second.jsonl", seed=42)

    assert first == second
    assert first.count(b"\n") > 100


def test_different_seed_produces_a_different_log(tmp_path: Path) -> None:
    baseline = write_log(tmp_path / "seed42.jsonl", seed=42)
    other = write_log(tmp_path / "seed7.jsonl", seed=7)

    assert baseline != other


def test_progress_hook_does_not_perturb_the_log() -> None:
    """The progress callback must be pure observation, or the CLI would change results."""
    without = ListSink()
    simulate(SHIFT_SECONDS, sink=without, seed=42)

    ticks: list[float] = []
    with_hook = ListSink()
    simulate(SHIFT_SECONDS, sink=with_hook, seed=42, on_progress=ticks.append)

    assert [event.to_dict() for event in without] == [event.to_dict() for event in with_hook]
    assert len(ticks) > 1


def test_simulation_never_touches_the_global_random_module() -> None:
    """A global-random call would make results depend on unrelated code."""
    random.seed(0)
    before = random.random()

    random.seed(0)
    simulate(SHIFT_SECONDS, seed=42)
    after = random.random()

    assert before == after


def test_distributions_are_driven_only_by_the_injected_rng() -> None:
    for distribution in (LogNormal(mean=30.0, cv=0.3), Triangular(low=1.0, mode=2.0, high=5.0)):
        first = [distribution.sample(random.Random(11)) for _ in range(3)]
        second = [distribution.sample(random.Random(11)) for _ in range(3)]
        assert first == second


def test_log_lines_are_valid_json_with_a_stable_schema(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    write_log(path, seed=42, minutes=60.0)

    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        assert list(record)[:4] == ["t", "event", "board", "station"]
        assert isinstance(record["t"], float)


def test_run_is_reproducible_across_independently_built_lines() -> None:
    """Building a fresh Line must not inherit state from an earlier one."""
    assert [e.to_dict() for e in run_events(seed=5)] == [e.to_dict() for e in run_events(seed=5)]
    assert DEFAULT_LINE.seed == 42


GOLDEN_NO_FAILURE_LOG_SHA256 = "a69523aacc819c7d70a7544200740c4153437c784a868f2b8a3fc6393c2c161c"


def test_the_default_line_log_matches_its_pinned_hash() -> None:
    """A tripwire on the default, failure-free line.

    Any change to the model, the stream derivation or the log format moves this
    hash. Nothing about stage 2 should: a line with no `failures` block must
    take exactly the path it took before breakdowns existed. Regenerate with::

        python -c "import hashlib, io; from smtsim.events import JsonlSink; \
        from smtsim.line import simulate; b = io.StringIO(); \
        simulate(3600.0, sink=JsonlSink(b), seed=42); \
        print(hashlib.sha256(b.getvalue().encode()).hexdigest())"
    """
    buffer = io.StringIO()
    simulate(60 * 60.0, sink=JsonlSink(buffer), seed=42)

    digest = hashlib.sha256(buffer.getvalue().encode("utf-8")).hexdigest()
    assert digest == GOLDEN_NO_FAILURE_LOG_SHA256
