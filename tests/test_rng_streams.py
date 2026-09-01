"""Named streams: changing one station must not disturb the others.

This is the property the whole of `smtsim compare` rests on. Without it, a
what-if run mixes the change under test with a reshuffled random sample.
"""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from smtsim.config import DEFAULT_LINE, SECONDS_PER_MINUTE, LineConfig, LogNormal, load_line_config
from smtsim.events import EventType, ListSink
from smtsim.line import simulate
from smtsim.rng import ARRIVALS_STREAM, RngStreams, derive_seed

SHIFT = 480 * SECONDS_PER_MINUTE

BUFFERED_LINE = load_line_config("configs/baseline.toml")

# Backpressure needs boards on the line before it can bite. Measured across
# seeds, the earliest the printer ever notices a change to the placer is board
# 11, so ten boards is a prefix the guarantee must cover in every run.
PREFIX_BOARDS = 10


def with_placer_capacity(config: LineConfig, capacity: int) -> LineConfig:
    stations = tuple(
        replace(station, capacity=capacity) if station.name == "pick_and_place" else station
        for station in config.stations
    )
    return replace(config, stations=stations)


def run_buffered(config: LineConfig, seed: int = 42) -> list:
    sink = ListSink()
    simulate(SHIFT, config=config, sink=sink, seed=seed)
    return sink.events


def timestamps(events, station: str) -> list[tuple[int, str, float]]:
    kinds = {EventType.SERVICE_STARTED, EventType.SERVICE_FINISHED}
    return [
        (e.board_id, str(e.type), e.time)
        for e in events
        if e.station == station and e.type in kinds
    ]


def run_with_placer(service_time, seed: int = 42) -> list:
    stations = tuple(
        replace(station, service_time=service_time) if station.name == "pick_and_place" else station
        for station in DEFAULT_LINE.stations
    )
    sink = ListSink()
    simulate(SHIFT, config=replace(DEFAULT_LINE, stations=stations), sink=sink, seed=seed)
    return sink.events


def test_with_unbounded_buffers_upstream_randomness_is_untouched_entirely() -> None:
    """With no buffer modelling, the printer is a function of arrivals alone.

    A board releases the printer the instant it is finished, whatever is
    happening downstream, so nothing about the placer can reach back up the
    line. This is the strongest form the guarantee takes, and it survives only
    because the line is uncoupled.
    """
    baseline = run_with_placer(LogNormal(mean=52.0, cv=0.20))
    variant = run_with_placer(LogNormal(mean=45.0, cv=0.30))

    printer = timestamps(baseline, "solder_paste_printer")
    assert printer, "expected the printer to have processed boards"
    assert printer == timestamps(variant, "solder_paste_printer")


def test_with_finite_buffers_the_guarantee_weakens_to_a_prefix() -> None:
    """Backpressure reaches the printer eventually, and must not before that.

    Stage 2b is where this test earns its keep. Once the conveyor in front of
    the placer can fill, a slower placer eventually blocks the printer, and from
    that moment the printer's timeline legitimately differs between the two
    configurations. Both halves matter:

    * for a leading prefix of boards -- before any backpressure could have
      propagated -- the printer must be *identical*, or the streams are leaking;
    * after that, it must *diverge*, or the buffers are not actually coupling
      the line and the first assertion is proving nothing.

    A test that only asserted the weaker half would still pass with the streams
    broken, which is why the divergence is asserted too.
    """
    baseline = run_buffered(BUFFERED_LINE)
    variant = run_buffered(with_placer_capacity(BUFFERED_LINE, 2))

    before = timestamps(baseline, "solder_paste_printer")
    after = timestamps(variant, "solder_paste_printer")

    prefix = [event for event in before if event[0] <= PREFIX_BOARDS]
    assert len(prefix) == 2 * PREFIX_BOARDS, "expected both events for each early board"
    assert prefix == [event for event in after if event[0] <= PREFIX_BOARDS]

    assert before != after, "finite buffers must let the change reach back upstream"
    first_difference = next(
        index for index, (a, b) in enumerate(zip(before, after, strict=False)) if a != b
    )
    assert first_difference > 2 * PREFIX_BOARDS


@pytest.mark.parametrize("seed", [1, 9, 42])
def test_the_prefix_guarantee_holds_across_seeds(seed: int) -> None:
    baseline = run_buffered(BUFFERED_LINE, seed=seed)
    variant = run_buffered(with_placer_capacity(BUFFERED_LINE, 2), seed=seed)

    def prefix(events):
        return [event for event in timestamps(events, "solder_paste_printer")
                if event[0] <= PREFIX_BOARDS]

    assert prefix(baseline) == prefix(variant)
    assert timestamps(baseline, "solder_paste_printer") != timestamps(
        variant, "solder_paste_printer"
    )


def test_changing_the_placer_leaves_arrivals_untouched() -> None:
    """Arrivals are drawn before the line is reached, so they never diverge."""
    for config in (DEFAULT_LINE, BUFFERED_LINE):
        baseline = run_buffered(config)
        variant = run_buffered(with_placer_capacity(config, 2))
        arrivals = [(e.board_id, e.time) for e in baseline if e.type is EventType.BOARD_ARRIVED]

        assert arrivals == [
            (e.board_id, e.time) for e in variant if e.type is EventType.BOARD_ARRIVED
        ]


def test_the_change_under_test_does_show_up_downstream() -> None:
    """A test that cannot fail proves nothing: the variant must differ somewhere."""
    baseline = run_with_placer(LogNormal(mean=52.0, cv=0.20))
    variant = run_with_placer(LogNormal(mean=45.0, cv=0.30))

    assert timestamps(baseline, "pick_and_place") != timestamps(variant, "pick_and_place")
    assert timestamps(baseline, "reflow_oven") != timestamps(variant, "reflow_oven")


def test_capacity_changes_also_leave_an_unbuffered_printer_untouched() -> None:
    """The canonical what-if, on the uncoupled line."""
    baseline = run_buffered(DEFAULT_LINE)
    variant = run_buffered(with_placer_capacity(DEFAULT_LINE, 2))

    assert timestamps(baseline, "solder_paste_printer") == timestamps(
        variant, "solder_paste_printer"
    )
    assert timestamps(baseline, "spi") != timestamps(variant, "spi")


def test_streams_are_independent_of_one_another() -> None:
    streams = RngStreams(master_seed=42)
    draws = {
        name: [streams.stream(name).random() for _ in range(5)]
        for name in (
            ARRIVALS_STREAM,
            "station:a:service",
            "station:a:failures",
            "station:b:service",
        )
    }

    assert len({tuple(values) for values in draws.values()}) == len(draws)


def test_a_stream_is_reproducible_and_seed_dependent() -> None:
    first = [RngStreams(7).station_service("spi").random() for _ in range(3)]
    again = [RngStreams(7).station_service("spi").random() for _ in range(3)]
    other = [RngStreams(8).station_service("spi").random() for _ in range(3)]

    assert first == again
    assert first != other


def test_derived_seeds_do_not_depend_on_the_interpreter_hash_salt() -> None:
    """Pinned values: built-in hash() would vary per process, blake2b does not."""
    assert derive_seed(42, ARRIVALS_STREAM) == 9089065230148016327
    assert derive_seed(42, "station:pick_and_place:service") == 2847977411752433390
    assert derive_seed(43, ARRIVALS_STREAM) != derive_seed(42, ARRIVALS_STREAM)


def test_streams_do_not_touch_the_global_random_module() -> None:
    random.seed(0)
    before = random.random()
    random.seed(0)
    RngStreams(42).station_service("spi").random()
    assert random.random() == before
