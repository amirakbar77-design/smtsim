"""Properties that must hold for any run, whatever the seed."""

from __future__ import annotations

import pytest

from conftest import run_events
from smtsim.events import Event, EventType
from smtsim.stats import summarise

SEEDS = (1, 42, 1234)


def test_boards_are_conserved(shift_events: list[Event]) -> None:
    """Every board that arrived either finished the line or is still on it."""
    arrived = {e.board_id for e in shift_events if e.type is EventType.BOARD_ARRIVED}
    completed = {e.board_id for e in shift_events if e.type is EventType.BOARD_COMPLETED}

    assert completed <= arrived

    stats = summarise(shift_events)
    assert stats.boards_arrived == len(arrived)
    assert stats.boards_completed == len(completed)
    assert stats.boards_arrived == stats.boards_completed + stats.boards_in_system
    assert stats.boards_in_system == len(arrived - completed)


@pytest.mark.parametrize("seed", SEEDS)
def test_event_timestamps_never_decrease(seed: int) -> None:
    events = run_events(seed=seed)
    times = [event.time for event in events]

    assert times == sorted(times)
    assert times[0] == 0.0


@pytest.mark.parametrize("seed", SEEDS)
def test_pick_and_place_is_the_bottleneck(seed: int) -> None:
    stats = summarise(run_events(seed=seed))
    utilisations = {station.name: station.utilisation for station in stats.stations}
    ranked = sorted(utilisations.items(), key=lambda item: item[1], reverse=True)

    assert ranked[0][0] == "pick_and_place"
    assert stats.bottleneck.name == "pick_and_place"
    assert ranked[0][1] > ranked[1][1]


def test_service_never_starts_before_the_board_joined_the_queue(shift_events: list[Event]) -> None:
    queued_at: dict[tuple[int, str], float] = {}
    started: set[tuple[int, str]] = set()

    for event in shift_events:
        if event.type is EventType.QUEUE_ENTERED:
            key = (event.board_id, event.station)
            assert key not in queued_at, f"{key} queued twice without being served"
            queued_at[key] = event.time
        elif event.type is EventType.SERVICE_STARTED:
            key = (event.board_id, event.station)
            assert key in queued_at, f"{key} started service without entering the queue"
            assert event.time >= queued_at[key]
            started.add(key)

    assert started


def test_each_board_visits_every_station_in_line_order(shift_events: list[Event]) -> None:
    expected = ["solder_paste_printer", "pick_and_place", "spi", "reflow_oven"]
    completed = {e.board_id for e in shift_events if e.type is EventType.BOARD_COMPLETED}
    visits: dict[int, list[str]] = {}

    for event in shift_events:
        if event.type is EventType.SERVICE_FINISHED and event.board_id in completed:
            visits.setdefault(event.board_id, []).append(event.station)

    assert visits
    for board_id, sequence in visits.items():
        assert sequence == expected, f"board {board_id} took the wrong route"


def test_a_station_never_serves_more_boards_than_its_capacity(shift_events: list[Event]) -> None:
    """Concurrency is enforced by the resource, not by the station code."""
    capacities = {station.name: station.capacity for station in summarise(shift_events).stations}
    in_service = dict.fromkeys(capacities, 0)

    for event in shift_events:
        if event.type is EventType.SERVICE_STARTED:
            in_service[event.station] += 1
            assert in_service[event.station] <= capacities[event.station]
        elif event.type is EventType.SERVICE_FINISHED:
            in_service[event.station] -= 1


def test_queues_form_at_the_bottleneck_without_being_programmed(shift_events: list[Event]) -> None:
    stats = summarise(shift_events)

    assert stats.station("pick_and_place").max_queue_length > 1
    assert stats.station("pick_and_place").mean_wait_seconds > 0.0


@pytest.mark.parametrize("seed", SEEDS)
def test_cycle_time_is_at_least_the_sum_of_service_times(seed: int) -> None:
    stats = summarise(run_events(seed=seed))
    minimum = sum(station.mean_service_seconds for station in stats.stations)

    assert stats.mean_cycle_time_seconds >= minimum
    assert stats.p95_cycle_time_seconds >= stats.mean_cycle_time_seconds
