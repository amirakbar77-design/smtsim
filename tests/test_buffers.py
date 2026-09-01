"""Finite inter-station buffers: blocking, starving, and the time accounting."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import replace

import pytest

from smtsim.config import (
    DEFAULT_LINE,
    SECONDS_PER_MINUTE,
    FailureConfig,
    LineConfig,
    load_line_config,
)
from smtsim.events import Event, EventType, JsonlSink, ListSink
from smtsim.line import simulate
from smtsim.stats import summarise

SHIFT = 480 * SECONDS_PER_MINUTE
BASELINE = load_line_config("configs/baseline.toml")
TIGHT = load_line_config("configs/tight_buffers.toml")
FLAKY = FailureConfig(mtbf=1800.0, mttr=420.0, mttr_cv=0.4)

CONVEYOR_STATIONS = ("pick_and_place", "spi", "reflow_oven")


def buffered(size: int | None, failing: tuple[str, ...] = (), capacity: int | None = None) -> LineConfig:
    """The default line with a uniform conveyor length, optionally flaky."""
    stations = tuple(
        replace(
            station,
            input_buffer=size if station.name in CONVEYOR_STATIONS else None,
            failures=FLAKY if station.name in failing else None,
            capacity=capacity if capacity and station.name == "pick_and_place" else station.capacity,
        )
        for station in DEFAULT_LINE.stations
    )
    return replace(DEFAULT_LINE, stations=stations)


def run(config: LineConfig, seed: int = 42, horizon: float = SHIFT) -> list[Event]:
    sink = ListSink()
    simulate(horizon, config=config, sink=sink, seed=seed)
    return sink.events


def blocked_intervals(events: list[Event], station: str) -> list[tuple[float, float]]:
    """Reconstruct blocked windows from the log, independently of stats.py."""
    intervals: list[tuple[float, float]] = []
    opened: dict[int, float] = {}
    for event in events:
        if event.station != station:
            continue
        if event.type is EventType.TRANSFER_BLOCKED:
            opened[event.board_id] = event.time
        elif event.type is EventType.TRANSFER_UNBLOCKED:
            intervals.append((opened.pop(event.board_id), event.time))
    return intervals


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


# --- compatibility ----------------------------------------------------------


def test_a_line_without_buffers_is_byte_identical_to_one_that_never_had_them() -> None:
    """The compatibility guarantee that matters most in this stage.

    A station with no `input_buffer` builds no buffer resource, makes no
    request and yields nowhere, so the board follows exactly the code path it
    followed before buffers existed. This is the same digest pinned in
    test_determinism.py, asserted here from the buffer side.
    """
    from test_determinism import GOLDEN_NO_FAILURE_MODEL_SHA256, model_digest

    assert all(station.input_buffer is None for station in DEFAULT_LINE.stations)
    assert model_digest() == GOLDEN_NO_FAILURE_MODEL_SHA256


def test_an_unbounded_config_never_emits_a_blocking_event() -> None:
    events = run(buffered(None, failing=("pick_and_place",)))

    assert not [e for e in events if e.type is EventType.TRANSFER_BLOCKED]
    assert all(station.blocked_slot_seconds == 0.0 for station in summarise(events).stations)


def test_explicitly_unbounded_and_omitted_buffers_agree(tmp_path) -> None:
    def digest(config: LineConfig) -> str:
        buffer = io.StringIO()
        simulate(60 * 60.0, config=config, sink=JsonlSink(buffer), seed=7)
        return hashlib.sha256(buffer.getvalue().encode()).hexdigest()

    assert digest(DEFAULT_LINE) == digest(buffered(None))


# --- the convention ---------------------------------------------------------


def test_a_board_frees_its_buffer_slot_when_the_machine_takes_it() -> None:
    """The queue is the conveyor, so it can never hold more than the conveyor does.

    A board occupies a slot from `queue_entered` until `service_started`: it is
    lifted off the conveyor and into the machine. So peak queue length is
    bounded by the buffer size, and the station can hold `capacity` boards on
    top of that.
    """
    for size in (1, 2, 3):
        stats = summarise(run(buffered(size)))
        for name in CONVEYOR_STATIONS:
            assert stats.station(name).max_queue_length <= size, (
                f"{name} queued more boards than its {size}-board conveyor holds"
            )
        assert stats.station("pick_and_place").max_queue_length == size, (
            "the bottleneck's conveyor should actually fill"
        )


def test_the_unbuffered_first_station_still_queues_freely() -> None:
    stats = summarise(run(buffered(1)))

    assert DEFAULT_LINE.station("solder_paste_printer").input_buffer is None
    assert stats.station("solder_paste_printer").max_queue_length >= 1


def test_the_last_station_never_blocks() -> None:
    """Completed boards leave the line, so there is nothing to block on."""
    events = run(buffered(1, failing=("pick_and_place", "reflow_oven")))
    last = DEFAULT_LINE.stations[-1].name

    assert not blocked_intervals(events, last)
    assert summarise(events).station(last).blocked_slot_seconds == 0.0


def test_shortening_the_conveyors_blocks_the_machines_behind_them() -> None:
    roomy = summarise(run(buffered(2))).station("solder_paste_printer")
    tight = summarise(run(buffered(1))).station("solder_paste_printer")

    assert tight.blocked_fraction > roomy.blocked_fraction > 0.0


def test_enough_slack_makes_the_coupling_disappear_again() -> None:
    """Three boards of conveyor absorb ordinary variation on a healthy line.

    Blocking is not a fact about finite buffers as such -- it is what happens
    when a buffer is too short for the variation it has to absorb. On this line
    a three-board conveyor is enough that the printer never once has to wait,
    and the model reduces to the uncoupled one. It takes a breakdown to fill it.
    """
    assert summarise(run(buffered(3))).station("solder_paste_printer").blocked_fraction == 0.0
    assert (
        summarise(run(buffered(3, failing=("pick_and_place",))))
        .station("solder_paste_printer")
        .blocked_fraction
        > 0.0
    )


# --- the four-way time accounting -------------------------------------------


@pytest.mark.parametrize("seed", [1, 7, 42, 1234])
@pytest.mark.parametrize(
    ("label", "config"),
    [
        ("unbounded", buffered(None)),
        ("generous", buffered(3)),
        ("tight", buffered(1)),
        ("tight, flaky", buffered(1, failing=("pick_and_place", "reflow_oven"))),
        ("generous, flaky", buffered(3, failing=("solder_paste_printer", "pick_and_place"))),
    ],
)
def test_the_four_accounts_partition_the_window(label: str, config: LineConfig, seed: int) -> None:
    """work + block + starve + down = the whole window, per unit of capacity.

    This is the sharpest structural check available. `starved` is not computed
    as the residual of the other three -- it comes from separately tracked
    station occupancy -- so the identity holds only if the number of boards
    physically inside a station always equals the number being worked on plus
    the number stuck waiting to leave. Mis-wire the buffers and it breaks.
    """
    stats = summarise(run(config, seed=seed), warmup_seconds=30 * SECONDS_PER_MINUTE)

    for station in stats.stations:
        assert station.measured_slot_seconds > 0
        assert station.accounted_slot_seconds == pytest.approx(
            station.measured_slot_seconds, rel=1e-9
        ), f"{label}/{station.name} does not account for its window"
        assert (
            station.working_fraction
            + station.blocked_fraction
            + station.starved_fraction
            + station.down_fraction
        ) == pytest.approx(1.0, abs=1e-9)
        for fraction in (
            station.working_fraction,
            station.blocked_fraction,
            station.starved_fraction,
            station.down_fraction,
        ):
            assert -1e-12 <= fraction <= 1.0 + 1e-12


def test_a_station_is_never_working_and_blocked_beyond_its_capacity() -> None:
    """Re-derived from the log, independently of the accumulator in stats.py."""
    events = run(buffered(1, failing=("pick_and_place", "reflow_oven")))
    capacities = {s.name: s.capacity for s in DEFAULT_LINE.stations}
    working = dict.fromkeys(capacities, 0)
    blocked = dict.fromkeys(capacities, 0)

    for event in events:
        if event.station not in capacities:
            continue
        match event.type:
            case EventType.SERVICE_STARTED | EventType.SERVICE_RESUMED:
                working[event.station] += 1
            case EventType.SERVICE_INTERRUPTED | EventType.SERVICE_FINISHED:
                working[event.station] -= 1
            case EventType.TRANSFER_BLOCKED:
                blocked[event.station] += 1
            case EventType.TRANSFER_UNBLOCKED:
                blocked[event.station] -= 1
        assert working[event.station] >= 0
        assert blocked[event.station] >= 0
        assert working[event.station] + blocked[event.station] <= capacities[event.station]


def test_blocked_time_matches_the_intervals_in_the_log() -> None:
    """Cross-check stats.py against an independent reduction of the same events."""
    events = run(buffered(1))
    stats = summarise(events)

    for name in ("solder_paste_printer", "pick_and_place", "spi"):
        expected = sum(end - start for start, end in blocked_intervals(events, name))
        assert stats.station(name).blocked_slot_seconds == pytest.approx(expected)


def test_every_block_is_closed() -> None:
    events = run(buffered(1, failing=("pick_and_place",)))
    open_blocks: set[tuple[int, str]] = set()

    for event in events:
        key = (event.board_id, event.station)
        if event.type is EventType.TRANSFER_BLOCKED:
            assert key not in open_blocks
            open_blocks.add(key)
        elif event.type is EventType.TRANSFER_UNBLOCKED:
            assert key in open_blocks
            open_blocks.discard(key)

    assert not open_blocks or len(open_blocks) <= len(DEFAULT_LINE.stations)


def test_a_block_always_follows_that_boards_service_finishing() -> None:
    """Blocking is after service, not before it: the machine completes the board."""
    events = run(buffered(1))
    finished: set[tuple[int, str]] = set()

    for event in events:
        if event.type is EventType.SERVICE_FINISHED:
            finished.add((event.board_id, event.station))
        elif event.type is EventType.TRANSFER_BLOCKED:
            assert (event.board_id, event.station) in finished


# --- backpressure -----------------------------------------------------------


def test_a_placer_breakdown_starves_the_spi_and_blocks_the_printer() -> None:
    """The clearest single demonstration that backpressure exists.

    With unbounded buffers a placer failure is invisible to its neighbours: the
    printer keeps working into an infinite queue and the SPI keeps draining one.
    With real conveyors the failure propagates in both directions.
    """
    config = buffered(1, failing=("pick_and_place",))
    events = run(config)
    windows = [w for w in failure_windows(events, "pick_and_place") if w[1] - w[0] > 300.0]
    assert windows, "expected at least one substantial placer breakdown"

    printer_blocks = blocked_intervals(events, "solder_paste_printer")
    spi_work = [
        (start, end)
        for start, end in _service_spans(events, "spi")
    ]

    blocked_during = 0
    starved_during = 0
    for start, end in windows:
        if any(bs < end and be > start for bs, be in printer_blocks):
            blocked_during += 1
        worked = sum(max(0.0, min(end, we) - max(start, ws)) for ws, we in spi_work)
        if worked < 0.5 * (end - start):
            starved_during += 1

    assert blocked_during == len(windows), "the printer should block behind a dead placer"
    assert starved_during == len(windows), "the SPI should starve in front of a dead placer"


def test_starvation_needs_no_buffers_but_blocking_does() -> None:
    """Only half of backpressure is new in this stage.

    Starving was already there: with unbounded buffers a dead placer still
    passes nothing downstream, so the SPI runs dry exactly as it does now.
    Blocking is what finite buffers add -- an uncoupled printer works happily
    into an infinite queue and never notices the machine ahead of it has
    stopped. This test pins the distinction so neither half is mistaken for
    the other.
    """
    events = run(buffered(None, failing=("pick_and_place",)))
    windows = [w for w in failure_windows(events, "pick_and_place") if w[1] - w[0] > 300.0]
    assert windows

    spi_work = _service_spans(events, "spi")
    starved_windows = 0
    for start, end in windows:
        worked = sum(max(0.0, min(end, we) - max(start, ws)) for ws, we in spi_work)
        if worked < 0.5 * (end - start):
            starved_windows += 1

    assert starved_windows == len(windows), "the SPI starves with or without buffers"
    assert not blocked_intervals(events, "solder_paste_printer"), (
        "but nothing can block without a finite buffer to fill"
    )


def _service_spans(events: list[Event], station: str) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    started: dict[int, float] = {}
    for event in events:
        if event.station != station:
            continue
        if event.type in {EventType.SERVICE_STARTED, EventType.SERVICE_RESUMED}:
            started[event.board_id] = event.time
        elif event.type in {EventType.SERVICE_INTERRUPTED, EventType.SERVICE_FINISHED}:
            if event.board_id in started:
                spans.append((started.pop(event.board_id), event.time))
    return spans


# --- breakdowns and blocking together ---------------------------------------


def test_a_blocked_station_does_not_wear_out() -> None:
    """Failures accrue on operating time, and blocking is not operating.

    The printer spends a large share of a tight-buffer run blocked. If that
    time counted towards its failure countdown its observed MTBF -- measured
    against operating time -- would come out well below the configured value.
    It must not.
    """
    observed: list[float] = []
    weights: list[int] = []
    blocked_fractions: list[float] = []

    for seed in range(1, 21):
        stats = summarise(run(buffered(1, failing=("solder_paste_printer",)), seed=seed))
        printer = stats.station("solder_paste_printer")
        blocked_fractions.append(printer.blocked_fraction)
        if printer.failures:
            observed.append(printer.observed_mtbf_seconds)
            weights.append(printer.failures)

    assert sum(blocked_fractions) / len(blocked_fractions) > 0.15, (
        "this test is meaningless unless the printer really is blocked a lot"
    )
    total = sum(weights)
    pooled = sum(m * w for m, w in zip(observed, weights, strict=True)) / total

    assert total > 60
    assert pooled == pytest.approx(FLAKY.mtbf, rel=0.12)


def test_shorter_conveyors_cost_throughput_although_no_machine_got_slower() -> None:
    """The effect is real, small, and invisible in any single run.

    Blocking upstream of the bottleneck is free as long as the bottleneck never
    runs dry, so shortening the conveyors costs output only through the placer
    starving a little more often. On several seeds the two configurations
    produce exactly the same number of boards. That is precisely the situation
    `smtsim compare` exists for, and it is why this scenario is a better
    demonstration of the tool than buying a second placer.
    """
    assert (
        TIGHT.station("pick_and_place").service_time
        == BASELINE.station("pick_and_place").service_time
    )

    seeds = range(1, 13)
    generous = [
        summarise(run(BASELINE, seed=s), warmup_seconds=1800.0).throughput_per_hour
        for s in seeds
    ]
    tight = [
        summarise(run(TIGHT, seed=s), warmup_seconds=1800.0).throughput_per_hour
        for s in seeds
    ]

    assert all(t <= g + 1e-9 for t, g in zip(tight, generous, strict=True))
    assert sum(tight) / len(tight) < sum(generous) / len(generous)
    assert any(t == pytest.approx(g) for t, g in zip(tight, generous, strict=True)), (
        "if every seed separated them, the paired comparison would be unnecessary"
    )


# --- deadlock ---------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3, 42])
def test_the_smallest_sensible_buffers_still_run_to_completion(seed: int) -> None:
    """A linear line with blocking-after-service cannot deadlock. See the README.

    Every station waits only on the one downstream of it, and the last station
    waits on nothing, so the wait-for graph is a strictly increasing chain with
    a terminating end. With one-board conveyors everywhere -- and breakdowns on
    top -- boards must still flow.
    """
    config = buffered(1, failing=("pick_and_place", "reflow_oven", "solder_paste_printer"))
    events = run(config, seed=seed)
    stats = summarise(events)

    assert stats.boards_completed > 100, "the line stalled"
    assert stats.boards_arrived == stats.boards_completed + stats.boards_in_system

    times = [event.time for event in events]
    assert times == sorted(times)

    last_completion = max(
        e.time for e in events if e.type is EventType.BOARD_COMPLETED
    )
    assert last_completion > 0.9 * SHIFT, "boards stopped completing before the horizon"


def test_a_single_slot_line_with_no_slack_still_flows() -> None:
    """The tightest configuration the validator allows."""
    stations = tuple(
        replace(station, input_buffer=None if index == 0 else 1, capacity=1)
        for index, station in enumerate(DEFAULT_LINE.stations)
    )
    stats = summarise(run(replace(DEFAULT_LINE, stations=stations)))

    assert stats.boards_completed > 50
    assert stats.boards_arrived == stats.boards_completed + stats.boards_in_system


def test_a_zero_length_buffer_is_rejected() -> None:
    with pytest.raises(ValueError, match="input_buffer"):
        replace(DEFAULT_LINE.stations[1], input_buffer=0)


def test_buffer_sizes_survive_a_config_round_trip() -> None:
    from smtsim.config import line_config_from_dict

    assert line_config_from_dict(BASELINE.to_dict()) == BASELINE
    assert BASELINE.station("pick_and_place").input_buffer == 3
    assert TIGHT.station("pick_and_place").input_buffer == 1
    assert BASELINE.station("solder_paste_printer").input_buffer is None
