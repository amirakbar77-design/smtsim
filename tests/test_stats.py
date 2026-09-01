"""Metrics are derived from the event stream and nothing else."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import run_events
from smtsim.events import Event, EventType, read_jsonl, write_jsonl
from smtsim.stats import percentile, summarise


def hand_built_log() -> list[Event]:
    """Two boards through one single-slot station, with a deliberate overlap."""
    line = {"name": "toy", "stations": [{"name": "press", "capacity": 1}]}
    return [
        Event(
            0.0, EventType.RUN_STARTED, detail={"horizon_seconds": 100.0, "seed": 3, "line": line}
        ),
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


def failure_log() -> list[Event]:
    """One station, one board, one breakdown -- all arithmetic checkable by hand.

    Horizon 100 s, capacity 1. The board is worked 0-20, the station is down
    20-50, the board is worked again 50-60. So: busy 30 s, downtime 30 s,
    uptime 70 s, operating time 30 s.
    """
    line = {"name": "toy", "stations": [{"name": "press", "capacity": 1}]}
    return [
        Event(0.0, EventType.RUN_STARTED, detail={"horizon_seconds": 100.0, "line": line}),
        Event(0.0, EventType.BOARD_ARRIVED, 1),
        Event(0.0, EventType.QUEUE_ENTERED, 1, "press"),
        Event(0.0, EventType.SERVICE_STARTED, 1, "press"),
        Event(20.0, EventType.STATION_FAILED, station="press"),
        Event(20.0, EventType.SERVICE_INTERRUPTED, 1, "press"),
        Event(50.0, EventType.STATION_REPAIRED, station="press"),
        Event(50.0, EventType.SERVICE_RESUMED, 1, "press"),
        Event(60.0, EventType.SERVICE_FINISHED, 1, "press"),
        Event(60.0, EventType.BOARD_COMPLETED, 1),
        Event(100.0, EventType.RUN_FINISHED, detail={"horizon_seconds": 100.0}),
    ]


def test_availability_arithmetic_matches_a_log_worked_out_by_hand() -> None:
    press = summarise(failure_log()).station("press")

    assert press.failures == 1
    assert press.downtime_seconds == pytest.approx(30.0)
    assert press.availability == pytest.approx(0.70)
    assert press.utilisation == pytest.approx(0.30)
    assert press.utilisation_uptime == pytest.approx(30.0 / 70.0)
    assert press.observed_mttr_seconds == pytest.approx(30.0)
    assert press.observed_mtbf_seconds == pytest.approx(30.0)
    assert press.mean_service_seconds == pytest.approx(30.0)


def test_downtime_still_open_at_the_horizon_counts_against_availability() -> None:
    line = {"name": "toy", "stations": [{"name": "press", "capacity": 1}]}
    events = [
        Event(0.0, EventType.RUN_STARTED, detail={"horizon_seconds": 100.0, "line": line}),
        Event(0.0, EventType.BOARD_ARRIVED, 1),
        Event(0.0, EventType.QUEUE_ENTERED, 1, "press"),
        Event(0.0, EventType.SERVICE_STARTED, 1, "press"),
        Event(40.0, EventType.STATION_FAILED, station="press"),
        Event(40.0, EventType.SERVICE_INTERRUPTED, 1, "press"),
        Event(100.0, EventType.RUN_FINISHED, detail={"horizon_seconds": 100.0}),
    ]
    press = summarise(events).station("press")

    assert press.downtime_seconds == pytest.approx(60.0)
    assert press.availability == pytest.approx(0.40)
    assert press.observed_mttr_seconds is None


def test_a_station_that_cannot_fail_is_fully_available() -> None:
    press = summarise(hand_built_log()).station("press")

    assert press.availability == 1.0
    assert press.failures == 0
    assert press.observed_mtbf_seconds is None
    assert press.utilisation_uptime == pytest.approx(press.utilisation)


def test_warmup_excludes_the_opening_stretch_from_every_metric() -> None:
    """Same hand-built log, measured over the last 40 s only.

    In [61, 100] the press is idle, the queue is empty and nothing completes,
    so throughput is zero and utilisation is zero -- while the un-warmed run
    reports 36 boards/hour over the same log.
    """
    events = failure_log()
    whole = summarise(events, warmup_seconds=0.0)
    tail = summarise(events, warmup_seconds=61.0)

    assert whole.boards_completed == 1
    assert tail.boards_completed == 0
    assert tail.window_seconds == pytest.approx(39.0)
    assert tail.throughput_per_hour == 0.0
    assert tail.station("press").utilisation == 0.0
    assert tail.station("press").availability == 1.0
    assert tail.station("press").failures == 0


def test_warmup_clips_a_failure_that_straddles_the_boundary() -> None:
    """Downtime inside the window still counts, even though the failure began before it."""
    stats = summarise(failure_log(), warmup_seconds=30.0)
    press = stats.station("press")

    assert stats.window_seconds == pytest.approx(70.0)
    assert press.downtime_seconds == pytest.approx(20.0)
    assert press.availability == pytest.approx(50.0 / 70.0)
    assert press.failures == 0, "the failure started before the window opened"
    assert press.observed_mttr_seconds is None
    assert press.utilisation == pytest.approx(10.0 / 70.0)


def test_summarise_adopts_the_warmup_recorded_in_the_log() -> None:
    """`smtsim stats` reproduces a run's own table without being told how."""
    line = {"name": "toy", "stations": [{"name": "press", "capacity": 1}]}
    events = [
        Event(
            0.0,
            EventType.RUN_STARTED,
            detail={"horizon_seconds": 100.0, "warmup_seconds": 61.0, "line": line},
        ),
        *failure_log()[1:],
    ]

    assert summarise(events).warmup_seconds == pytest.approx(61.0)
    assert summarise(events).boards_completed == 0
    assert summarise(events, warmup_seconds=0.0).boards_completed == 1


def test_the_queue_level_at_the_moment_the_window_opens_is_not_lost() -> None:
    """A queue that formed during warm-up and never grows again still counts."""
    line = {"name": "toy", "stations": [{"name": "press", "capacity": 1}]}
    events = [
        Event(0.0, EventType.RUN_STARTED, detail={"horizon_seconds": 100.0, "line": line}),
        Event(0.0, EventType.QUEUE_ENTERED, 1, "press"),
        Event(0.0, EventType.SERVICE_STARTED, 1, "press"),
        Event(1.0, EventType.QUEUE_ENTERED, 2, "press"),
        Event(2.0, EventType.QUEUE_ENTERED, 3, "press"),
        Event(90.0, EventType.SERVICE_FINISHED, 1, "press"),
        Event(100.0, EventType.RUN_FINISHED, detail={"horizon_seconds": 100.0}),
    ]

    assert summarise(events, warmup_seconds=50.0).station("press").max_queue_length == 2


def test_the_measurement_window_includes_its_own_opening_instant() -> None:
    """The window is [warmup, horizon]: an event exactly on the boundary is in."""
    events = failure_log()

    assert summarise(events, warmup_seconds=60.0).boards_completed == 1
    assert summarise(events, warmup_seconds=60.000001).boards_completed == 0
