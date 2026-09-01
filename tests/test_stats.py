"""Metrics are derived from the event stream and nothing else."""

from __future__ import annotations

from pathlib import Path

import pytest

from smtsim.events import Event, EventType, read_jsonl, write_jsonl
from smtsim.stats import percentile, summarise

from conftest import run_events


def hand_built_log() -> list[Event]:
    """Two boards through one single-slot station, with a deliberate overlap."""
    line = {"name": "toy", "stations": [{"name": "press", "capacity": 1}]}
    return [
        Event(0.0, EventType.RUN_STARTED, detail={"horizon_seconds": 100.0, "seed": 3, "line": line}),
        Event(0.0, EventType.BOARD_ARRIVED, 1),
        Event(0.0, EventType.QUEUE_ENTERED, 1, "press"),
        Event(0.0, EventType.SERVICE_STARTED, 1, "press"),
        Event(5.0, EventType.BOARD_ARRIVED, 2),
        Event(5.0, EventType.QUEUE_ENTERED, 2, "press"),
        Event(30.0, EventType.SERVICE_FINISHED, 1, "press"),
        Event(30.0, EventType.BOARD_COMPLETED, 1),
        Event(30.0, EventType.SERVICE_STARTED, 2, "press"),
        Event(40.0, EventType.SERVICE_FINISHED, 2, "press"),
        Event(40.0, EventType.BOARD_COMPLETED, 2),
        Event(100.0, EventType.RUN_FINISHED, detail={"horizon_seconds": 100.0}),
    ]


def test_metrics_match_a_log_worked_out_by_hand() -> None:
    stats = summarise(hand_built_log())

    assert stats.boards_arrived == 2
    assert stats.boards_completed == 2
    assert stats.boards_in_system == 0
    assert stats.throughput_per_hour == pytest.approx(72.0)
    assert stats.mean_cycle_time_seconds == pytest.approx(32.5)

    press = stats.station("press")
    assert press.utilisation == pytest.approx(0.40)
    assert press.max_queue_length == 1
    assert press.mean_wait_seconds == pytest.approx(12.5)
    assert press.mean_service_seconds == pytest.approx(20.0)


def test_utilisation_of_a_multi_slot_station_accounts_for_capacity() -> None:
    line = {"name": "toy", "stations": [{"name": "oven", "capacity": 4}]}
    events = [
        Event(0.0, EventType.RUN_STARTED, detail={"horizon_seconds": 100.0, "line": line}),
        Event(0.0, EventType.BOARD_ARRIVED, 1),
        Event(0.0, EventType.QUEUE_ENTERED, 1, "oven"),
        Event(0.0, EventType.SERVICE_STARTED, 1, "oven"),
        Event(80.0, EventType.SERVICE_FINISHED, 1, "oven"),
        Event(100.0, EventType.RUN_FINISHED, detail={"horizon_seconds": 100.0}),
    ]

    assert summarise(events).station("oven").utilisation == pytest.approx(0.2)


def test_service_still_running_at_the_horizon_counts_as_busy() -> None:
    line = {"name": "toy", "stations": [{"name": "press", "capacity": 1}]}
    events = [
        Event(0.0, EventType.RUN_STARTED, detail={"horizon_seconds": 100.0, "line": line}),
        Event(60.0, EventType.BOARD_ARRIVED, 1),
        Event(60.0, EventType.QUEUE_ENTERED, 1, "press"),
        Event(60.0, EventType.SERVICE_STARTED, 1, "press"),
        Event(100.0, EventType.RUN_FINISHED, detail={"horizon_seconds": 100.0}),
    ]

    assert summarise(events).station("press").utilisation == pytest.approx(0.4)


def test_stats_from_a_saved_log_match_stats_computed_in_memory(tmp_path: Path) -> None:
    """`smtsim stats` must not disagree with the table printed after a run."""
    events = run_events(seed=99)
    path = tmp_path / "run.jsonl"
    write_jsonl(path, events)

    assert summarise(read_jsonl(path)) == summarise(events)


def test_percentile_interpolates_between_neighbours() -> None:
    values = [float(n) for n in range(1, 11)]

    assert percentile(list(values), 0.0) == 1.0
    assert percentile(list(values), 1.0) == 10.0
    assert percentile(list(values), 0.5) == pytest.approx(5.5)
    assert percentile(list(values), 0.95) == pytest.approx(9.55)
    assert percentile([4.0], 0.95) == 4.0


def test_percentile_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        percentile([], 0.5)
    with pytest.raises(ValueError):
        percentile([1.0], 1.5)


def test_a_log_without_a_header_still_summarises() -> None:
    """Logs trimmed by hand or streamed from a socket lack the run_started line."""
    events = [event for event in hand_built_log() if event.type is not EventType.RUN_STARTED]
    stats = summarise(events)

    assert stats.boards_completed == 2
    assert stats.station("press").capacity == 1
    assert stats.horizon_seconds == 100.0


def test_reading_a_corrupt_log_reports_the_line_number(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text('{"t":0.0,"event":"board_arrived","board":1,"station":null}\nnot json\n')

    with pytest.raises(ValueError, match="broken.jsonl:2"):
        list(read_jsonl(path))
