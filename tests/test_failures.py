"""Machine breakdowns: interruption, resumption, and the reliability metrics."""

from __future__ import annotations

from dataclasses import replace

import pytest

from smtsim.config import DEFAULT_LINE, SECONDS_PER_MINUTE, FailureConfig, LineConfig
from smtsim.events import Event, EventType, ListSink
from smtsim.line import simulate
from smtsim.stats import summarise

SHIFT = 480 * SECONDS_PER_MINUTE
FLAKY = FailureConfig(mtbf=1200.0, mttr=300.0, mttr_cv=0.4)


def line_with_failures(failures: FailureConfig = FLAKY, *names: str) -> LineConfig:
    targets = set(names) or {"pick_and_place"}
    stations = tuple(
        replace(station, failures=failures) if station.name in targets else station
        for station in DEFAULT_LINE.stations
    )
    return replace(DEFAULT_LINE, stations=stations)


def run(config: LineConfig, seed: int = 42, horizon: float = SHIFT) -> list[Event]:
    sink = ListSink()
    simulate(horizon, config=config, sink=sink, seed=seed)
    return sink.events


def failure_windows(events: list[Event], station: str) -> list[tuple[float, float]]:
    windows: list[tuple[float, float]] = []
    opened: float | None = None
    for event in events:
        if event.station != station:
            continue
        if event.type is EventType.STATION_FAILED:
            opened = event.time
        elif event.type is EventType.STATION_REPAIRED and opened is not None:
            windows.append((opened, event.time))
            opened = None
    return windows


def test_a_station_without_a_failures_block_never_fails() -> None:
    events = run(DEFAULT_LINE)

    assert not [e for e in events if e.type is EventType.STATION_FAILED]
    assert summarise(events).station("pick_and_place").availability == 1.0


def test_failures_and_repairs_alternate_and_are_paired() -> None:
    events = run(line_with_failures())
    sequence = [
        e.type
        for e in events
        if e.type in {EventType.STATION_FAILED, EventType.STATION_REPAIRED}
    ]

    assert sequence, "expected the flaky station to fail at least once"
    assert sequence[0] is EventType.STATION_FAILED
    for earlier, later in zip(sequence[::2], sequence[1::2], strict=False):
        assert earlier is EventType.STATION_FAILED
        assert later is EventType.STATION_REPAIRED


def test_no_service_finishes_while_the_station_is_down() -> None:
    events = run(line_with_failures(FLAKY, "pick_and_place", "reflow_oven"))

    for station in ("pick_and_place", "reflow_oven"):
        windows = failure_windows(events, station)
        assert windows, f"expected {station} to fail"
        finishes = [
            e.time
            for e in events
            if e.station == station and e.type is EventType.SERVICE_FINISHED
        ]
        for start, end in windows:
            inside = [t for t in finishes if start < t < end]
            assert not inside, f"{station} finished a board at {inside} while down"


def test_no_service_starts_while_the_station_is_down() -> None:
    events = run(line_with_failures())
    windows = failure_windows(events, "pick_and_place")
    starts = [
        e.time
        for e in events
        if e.station == "pick_and_place"
        and e.type in {EventType.SERVICE_STARTED, EventType.SERVICE_RESUMED}
    ]

    for start, end in windows:
        assert not [t for t in starts if start < t < end]


def test_interrupted_work_resumes_rather_than_restarting() -> None:
    """A board 40 s into a 52 s placement must resume with 12 s left.

    The log records when work paused and when it restarted, so the time the
    board actually spent under the head can be added up and compared with an
    uninterrupted board's service time drawn from the same distribution.
    """
    events = run(line_with_failures())
    worked: dict[int, float] = {}
    since: dict[int, float] = {}
    interrupted: set[int] = set()
    totals: list[tuple[int, float, bool]] = []

    for event in events:
        if event.station != "pick_and_place":
            continue
        if event.type in {EventType.SERVICE_STARTED, EventType.SERVICE_RESUMED}:
            since[event.board_id] = event.time
        elif event.type in {EventType.SERVICE_INTERRUPTED, EventType.SERVICE_FINISHED}:
            worked[event.board_id] = worked.get(event.board_id, 0.0) + (
                event.time - since.pop(event.board_id)
            )
            if event.type is EventType.SERVICE_INTERRUPTED:
                interrupted.add(event.board_id)
            else:
                totals.append(
                    (
                        event.board_id,
                        worked.pop(event.board_id),
                        event.board_id in interrupted,
                    )
                )

    resumed = [work for _, work, was_interrupted in totals if was_interrupted]
    clean = [work for _, work, was_interrupted in totals if not was_interrupted]

    assert resumed, "expected at least one board to be interrupted mid-placement"
    mean_clean = sum(clean) / len(clean)
    mean_resumed = sum(resumed) / len(resumed)

    assert mean_resumed == pytest.approx(mean_clean, rel=0.25)

    wall_clock: dict[int, list[float]] = {}
    for event in events:
        if event.station == "pick_and_place" and event.type in {
            EventType.SERVICE_STARTED,
            EventType.SERVICE_FINISHED,
        }:
            wall_clock.setdefault(event.board_id, []).append(event.time)
    stretched = [
        board for board, (start, end) in
        ((b, t) for b, t in wall_clock.items() if len(t) == 2)
        if end - start > 1.5 * mean_clean and board in interrupted
    ]
    assert stretched, "an interrupted board should sit at the station longer than it is worked on"


def test_a_boards_wall_clock_at_a_station_covers_work_plus_downtime() -> None:
    events = run(line_with_failures())
    windows = failure_windows(events, "pick_and_place")
    spans: dict[int, dict[str, float]] = {}

    for event in events:
        if event.station != "pick_and_place":
            continue
        if event.type is EventType.SERVICE_STARTED:
            spans.setdefault(event.board_id, {})["start"] = event.time
        elif event.type is EventType.SERVICE_FINISHED:
            spans.setdefault(event.board_id, {})["end"] = event.time

    for _board, span in spans.items():
        if "start" not in span or "end" not in span:
            continue
        overlap = sum(
            max(0.0, min(span["end"], end) - max(span["start"], start))
            for start, end in windows
        )
        assert span["end"] - span["start"] >= overlap - 1e-9


@pytest.mark.parametrize("station_name", ["pick_and_place", "reflow_oven"])
def test_observed_mtbf_and_mttr_converge_on_the_configured_values(station_name: str) -> None:
    """Averaged over many seeds, the model must reproduce its own inputs.

    MTBF is compared against *operating* time, which is the clock the failure
    process actually consumes. Tolerance is wide because the number of failures
    per run is small: roughly 20 per shift here, so the standard error on the
    mean of an exponential sample is around 5% even pooled across 40 runs.
    """
    config = line_with_failures(FLAKY, station_name)
    observed_mtbf: list[float] = []
    observed_mttr: list[float] = []
    weights: list[int] = []

    for seed in range(1, 41):
        stats = summarise(run(config, seed=seed))
        station = stats.station(station_name)
        if station.failures == 0:
            continue
        observed_mtbf.append(station.observed_mtbf_seconds)
        observed_mttr.append(station.observed_mttr_seconds)
        weights.append(station.failures)

    total = sum(weights)
    pooled_mtbf = sum(m * w for m, w in zip(observed_mtbf, weights, strict=True)) / total
    pooled_mttr = sum(m * w for m, w in zip(observed_mttr, weights, strict=True)) / total

    assert total > 100, "not enough failures observed to say anything"
    assert pooled_mtbf == pytest.approx(FLAKY.mtbf, rel=0.10)
    assert pooled_mttr == pytest.approx(FLAKY.mttr, rel=0.10)


def test_failures_consume_operating_time_not_calendar_time() -> None:
    """An idle machine does not wear out.

    The SPI station is lightly loaded, so under calendar-time failures it would
    fail about as often as the placer. Under operating-time failures it fails
    far less, in proportion to how much of the shift it spends working.
    """
    placer = summarise(run(line_with_failures(FLAKY, "pick_and_place"))).station("pick_and_place")
    spi = summarise(run(line_with_failures(FLAKY, "spi"))).station("spi")

    assert placer.failures > spi.failures
    assert spi.failures > 0
    ratio = spi.failures / placer.failures
    assert ratio == pytest.approx(spi.utilisation / placer.utilisation, rel=0.6)


def test_downtime_and_availability_agree_with_the_failure_windows() -> None:
    events = run(line_with_failures())
    stats = summarise(events)
    station = stats.station("pick_and_place")
    expected = sum(end - start for start, end in failure_windows(events, "pick_and_place"))

    assert station.downtime_seconds == pytest.approx(expected)
    assert station.availability == pytest.approx(1.0 - expected / SHIFT)
    assert station.utilisation_uptime > station.utilisation


def test_conservation_monotonicity_and_causality_survive_breakdowns() -> None:
    events = run(line_with_failures(FLAKY, "pick_and_place", "reflow_oven", "spi"))
    stats = summarise(events)

    times = [event.time for event in events]
    assert times == sorted(times)

    arrived = {e.board_id for e in events if e.type is EventType.BOARD_ARRIVED}
    completed = {e.board_id for e in events if e.type is EventType.BOARD_COMPLETED}
    assert completed <= arrived
    assert stats.boards_arrived == stats.boards_completed + stats.boards_in_system

    queued: dict[tuple[int, str], float] = {}
    for event in events:
        key = (event.board_id, event.station)
        if event.type is EventType.QUEUE_ENTERED:
            queued[key] = event.time
        elif event.type is EventType.SERVICE_STARTED:
            assert key in queued
            assert event.time >= queued.pop(key)


def test_a_station_never_works_on_more_boards_than_its_capacity_under_failures() -> None:
    events = run(line_with_failures(FLAKY, "reflow_oven"))
    working = 0
    for event in events:
        if event.station != "reflow_oven":
            continue
        if event.type in {EventType.SERVICE_STARTED, EventType.SERVICE_RESUMED}:
            working += 1
            assert working <= DEFAULT_LINE.station("reflow_oven").capacity
        elif event.type in {EventType.SERVICE_INTERRUPTED, EventType.SERVICE_FINISHED}:
            working -= 1
    assert 0 <= working <= DEFAULT_LINE.station("reflow_oven").capacity, (
        "boards still in the tunnel when the horizon arrives never finish, which is fine, "
        "but the count must never go negative or exceed capacity"
    )


def test_a_failure_interrupts_every_board_inside_the_oven() -> None:
    """The belt stops, so a tunnel failure hits all its boards at once."""
    events = run(line_with_failures(FLAKY, "reflow_oven"))
    interrupts_per_failure: list[int] = []
    count = 0
    open_failure = False

    for event in events:
        if event.station != "reflow_oven":
            continue
        if event.type is EventType.STATION_FAILED:
            open_failure, count = True, 0
        elif event.type is EventType.SERVICE_INTERRUPTED and open_failure:
            count += 1
        elif event.type is EventType.STATION_REPAIRED and open_failure:
            interrupts_per_failure.append(count)
            open_failure = False

    assert interrupts_per_failure
    assert max(interrupts_per_failure) > 1
