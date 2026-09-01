"""Named streams: changing one station must not disturb the others.

This is the property the whole of `smtsim compare` rests on. Without it, a
what-if run mixes the change under test with a reshuffled random sample.
"""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from smtsim.config import DEFAULT_LINE, SECONDS_PER_MINUTE, LogNormal
from smtsim.events import EventType, ListSink
from smtsim.line import simulate
from smtsim.rng import ARRIVALS_STREAM, RngStreams, derive_seed

SHIFT = 480 * SECONDS_PER_MINUTE


def timestamps(events, station: str) -> list[tuple[int, str, float]]:
    kinds = {EventType.SERVICE_STARTED, EventType.SERVICE_FINISHED}
    return [(e.board_id, str(e.type), e.time) for e in events if e.station == station and e.type in kinds]


def run_with_placer(service_time, seed: int = 42) -> list:
    stations = tuple(
        replace(station, service_time=service_time) if station.name == "pick_and_place" else station
        for station in DEFAULT_LINE.stations
    )
    sink = ListSink()
    simulate(SHIFT, config=replace(DEFAULT_LINE, stations=stations), sink=sink, seed=seed)
    return sink.events


def test_changing_the_placer_leaves_upstream_randomness_untouched() -> None:
    """The printer is upstream of the change, so it must see an identical run.

    The brief expected this to hold only for the first few boards, until
    queueing feedback propagates upstream. It in fact holds for every board,
    and the reason is a documented limitation rather than a strength: buffers
    between stations are unbounded, so a backed-up placer never blocks the
    printer. When stage 2b adds finite buffers this assertion will have to
    weaken to a prefix of boards -- and that will be a sign the model got more
    realistic, not that the streams broke.
    """
    baseline = run_with_placer(LogNormal(mean=52.0, cv=0.20))
    variant = run_with_placer(LogNormal(mean=45.0, cv=0.30))

    printer = timestamps(baseline, "solder_paste_printer")
    assert printer, "expected the printer to have processed boards"
    assert printer == timestamps(variant, "solder_paste_printer")


def test_changing_the_placer_leaves_arrivals_untouched() -> None:
    baseline = run_with_placer(LogNormal(mean=52.0, cv=0.20))
    variant = run_with_placer(LogNormal(mean=45.0, cv=0.30))

    arrivals = [(e.board_id, e.time) for e in baseline if e.type is EventType.BOARD_ARRIVED]
    assert arrivals == [(e.board_id, e.time) for e in variant if e.type is EventType.BOARD_ARRIVED]


def test_the_change_under_test_does_show_up_downstream() -> None:
    """A test that cannot fail proves nothing: the variant must differ somewhere."""
    baseline = run_with_placer(LogNormal(mean=52.0, cv=0.20))
    variant = run_with_placer(LogNormal(mean=45.0, cv=0.30))

    assert timestamps(baseline, "pick_and_place") != timestamps(variant, "pick_and_place")
    assert timestamps(baseline, "reflow_oven") != timestamps(variant, "reflow_oven")


def test_capacity_changes_also_leave_the_printer_untouched() -> None:
    """The canonical what-if: a second placement head."""
    stations = tuple(
        replace(station, capacity=2) if station.name == "pick_and_place" else station
        for station in DEFAULT_LINE.stations
    )
    baseline = ListSink()
    variant = ListSink()
    simulate(SHIFT, sink=baseline, seed=42)
    simulate(SHIFT, config=replace(DEFAULT_LINE, stations=stations), sink=variant, seed=42)

    assert timestamps(baseline.events, "solder_paste_printer") == timestamps(
        variant.events, "solder_paste_printer"
    )
    assert timestamps(baseline.events, "spi") != timestamps(variant.events, "spi")


def test_streams_are_independent_of_one_another() -> None:
    streams = RngStreams(master_seed=42)
    draws = {
        name: [streams.stream(name).random() for _ in range(5)]
        for name in (ARRIVALS_STREAM, "station:a:service", "station:a:failures", "station:b:service")
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
