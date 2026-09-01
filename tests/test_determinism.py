"""The same seed must reproduce a run exactly, byte for byte."""

from __future__ import annotations

import hashlib
import io
import json
import random
from pathlib import Path

from dataclasses import replace

from smtsim.config import DEFAULT_LINE, FailureConfig, LogNormal, Triangular
from smtsim.events import EventType, JsonlSink, ListSink, open_jsonl
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


GOLDEN_NO_FAILURE_MODEL_SHA256 = "c22889bab140cacfe9be0e3df87f9602a532719e6cae837dc1e261ac6092fa6b"

RUN_METADATA = {EventType.RUN_STARTED, EventType.RUN_FINISHED}


def model_digest(minutes: float = 60.0, seed: int = 42, config=DEFAULT_LINE) -> str:
    """Hash the board-level events of a run.

    `run_started` and `run_finished` are excluded on purpose. They carry run
    metadata that may legitimately gain fields -- stage 2 added `warmup_seconds`
    -- and hashing them would turn every format addition into a false alarm
    about the model. What must not move is the sequence of things that happened
    on the line.
    """
    sink = ListSink()
    simulate(minutes * 60.0, config=config, sink=sink, seed=seed)
    payload = "".join(
        json.dumps(event.to_dict(), separators=(",", ":")) + "\n"
        for event in sink
        if event.type not in RUN_METADATA
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_the_default_line_model_matches_its_pinned_hash() -> None:
    """A tripwire on the default, failure-free line.

    A line with no `failures` block must take exactly the path it took before
    breakdowns existed: same arrivals, same service times, same order. Adding
    the availability machinery starts no process for such a station, so this
    hash is unchanged by stage 2. Regenerate it by printing `model_digest()`.
    """
    assert model_digest() == GOLDEN_NO_FAILURE_MODEL_SHA256


def test_adding_failures_to_one_station_does_not_disturb_the_others() -> None:
    """The failure stream is separate, so an unaffected station is untouched."""
    stations = tuple(
        replace(station, failures=FailureConfig(mtbf=1800.0, mttr=300.0))
        if station.name == "pick_and_place"
        else station
        for station in DEFAULT_LINE.stations
    )
    with_failures = ListSink()
    baseline = ListSink()
    simulate(SHIFT_SECONDS, sink=baseline, seed=42)
    simulate(SHIFT_SECONDS, config=replace(DEFAULT_LINE, stations=stations), sink=with_failures, seed=42)

    def printer(sink):
        return [(e.board_id, str(e.type), e.time) for e in sink if e.station == "solder_paste_printer"]

    assert printer(baseline) == printer(with_failures)
    assert any(e.type is EventType.STATION_FAILED for e in with_failures)
