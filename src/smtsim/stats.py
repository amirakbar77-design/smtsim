"""Summary metrics computed from an event log.

Everything here is derived from the event stream alone, so `smtsim stats` on a
saved log and the table printed at the end of a run go through exactly the same
code. The simulation itself keeps no counters for reporting.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from smtsim.config import SECONDS_PER_HOUR, SECONDS_PER_MINUTE
from smtsim.events import Event, EventType


def percentile(values: list[float], fraction: float) -> float:
    """Linearly interpolated percentile of ``values`` (which is sorted in place)."""
    if not values:
        raise ValueError("percentile of an empty sequence is undefined")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be between 0 and 1")
    values.sort()
    if len(values) == 1:
        return values[0]
    position = fraction * (len(values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


@dataclass(frozen=True, slots=True)
class StationStats:
    """Per-station metrics over the run."""

    name: str
    capacity: int
    utilisation: float
    boards_started: int
    boards_finished: int
    max_queue_length: int
    mean_wait_seconds: float
    mean_service_seconds: float


@dataclass(frozen=True, slots=True)
class LineStats:
    """Whole-line metrics over the run."""

    line_name: str
    seed: int | None
    horizon_seconds: float
    boards_arrived: int
    boards_completed: int
    boards_in_system: int
    throughput_per_hour: float
    mean_cycle_time_seconds: float
    p95_cycle_time_seconds: float
    stations: tuple[StationStats, ...]

    @property
    def horizon_minutes(self) -> float:
        return self.horizon_seconds / SECONDS_PER_MINUTE

    @property
    def mean_cycle_time_minutes(self) -> float:
        return self.mean_cycle_time_seconds / SECONDS_PER_MINUTE

    @property
    def p95_cycle_time_minutes(self) -> float:
        return self.p95_cycle_time_seconds / SECONDS_PER_MINUTE

    @property
    def bottleneck(self) -> StationStats | None:
        if not self.stations:
            return None
        return max(self.stations, key=lambda station: station.utilisation)

    def station(self, name: str) -> StationStats:
        for station in self.stations:
            if station.name == name:
                return station
        raise KeyError(name)


@dataclass(slots=True)
class _StationAccumulator:
    name: str
    order: int
    capacity: int = 1
    busy_seconds: float = 0.0
    boards_started: int = 0
    boards_finished: int = 0
    queue_length: int = 0
    max_queue_length: int = 0
    wait_seconds: float = 0.0
    waits_counted: int = 0
    service_seconds: float = 0.0
    services_counted: int = 0
    queued_at: dict[int, float] = field(default_factory=dict)
    started_at: dict[int, float] = field(default_factory=dict)


def summarise(events: Iterable[Event]) -> LineStats:
    """Reduce an event stream to a :class:`LineStats`.

    The stream is consumed once and never held in memory in full, so this works
    on logs far larger than RAM.
    """
    stations: dict[str, _StationAccumulator] = {}
    arrival_times: dict[int, float] = {}
    cycle_times: list[float] = []
    boards_arrived = 0
    boards_completed = 0
    line_name = "unknown line"
    seed: int | None = None
    horizon_seconds = 0.0
    horizon_declared = False
    last_time = 0.0

    def accumulator(name: str) -> _StationAccumulator:
        existing = stations.get(name)
        if existing is None:
            existing = _StationAccumulator(name=name, order=len(stations))
            stations[name] = existing
        return existing

    for event in events:
        last_time = max(last_time, event.time)

        match event.type:
            case EventType.RUN_STARTED:
                detail = event.detail or {}
                seed = detail.get("seed")
                horizon = detail.get("horizon_seconds")
                if horizon is not None:
                    horizon_seconds = float(horizon)
                    horizon_declared = True
                line = detail.get("line") or {}
                line_name = line.get("name", line_name)
                for spec in line.get("stations", []):
                    station = accumulator(spec["name"])
                    station.capacity = int(spec.get("capacity", 1))

            case EventType.BOARD_ARRIVED:
                boards_arrived += 1
                if event.board_id is not None:
                    arrival_times[event.board_id] = event.time

            case EventType.QUEUE_ENTERED:
                station = accumulator(event.station or "")
                station.queue_length += 1
                station.max_queue_length = max(station.max_queue_length, station.queue_length)
                if event.board_id is not None:
                    station.queued_at[event.board_id] = event.time

            case EventType.SERVICE_STARTED:
                station = accumulator(event.station or "")
                station.queue_length = max(0, station.queue_length - 1)
                station.boards_started += 1
                if event.board_id is not None:
                    station.started_at[event.board_id] = event.time
                    queued = station.queued_at.pop(event.board_id, None)
                    if queued is not None:
                        station.wait_seconds += event.time - queued
                        station.waits_counted += 1

            case EventType.SERVICE_FINISHED:
                station = accumulator(event.station or "")
                station.boards_finished += 1
                if event.board_id is not None:
                    started = station.started_at.pop(event.board_id, None)
                    if started is not None:
                        station.busy_seconds += event.time - started
                        station.service_seconds += event.time - started
                        station.services_counted += 1

            case EventType.BOARD_COMPLETED:
                boards_completed += 1
                if event.board_id is not None:
                    arrived = arrival_times.get(event.board_id)
                    if arrived is not None:
                        cycle_times.append(event.time - arrived)

            case EventType.RUN_FINISHED:
                detail = event.detail or {}
                horizon = detail.get("horizon_seconds")
                if horizon is not None:
                    horizon_seconds = float(horizon)
                    horizon_declared = True

    if not horizon_declared:
        horizon_seconds = last_time

    for station in stations.values():
        for started in station.started_at.values():
            station.busy_seconds += max(0.0, horizon_seconds - started)

    station_stats = tuple(
        StationStats(
            name=station.name,
            capacity=station.capacity,
            utilisation=(
                station.busy_seconds / (horizon_seconds * station.capacity)
                if horizon_seconds > 0
                else 0.0
            ),
            boards_started=station.boards_started,
            boards_finished=station.boards_finished,
            max_queue_length=station.max_queue_length,
            mean_wait_seconds=(
                station.wait_seconds / station.waits_counted if station.waits_counted else 0.0
            ),
            mean_service_seconds=(
                station.service_seconds / station.services_counted
                if station.services_counted
                else 0.0
            ),
        )
        for station in sorted(stations.values(), key=lambda item: item.order)
    )

    return LineStats(
        line_name=line_name,
        seed=seed,
        horizon_seconds=horizon_seconds,
        boards_arrived=boards_arrived,
        boards_completed=boards_completed,
        boards_in_system=boards_arrived - boards_completed,
        throughput_per_hour=(
            boards_completed * SECONDS_PER_HOUR / horizon_seconds if horizon_seconds > 0 else 0.0
        ),
        mean_cycle_time_seconds=(sum(cycle_times) / len(cycle_times) if cycle_times else 0.0),
        p95_cycle_time_seconds=(percentile(list(cycle_times), 0.95) if cycle_times else 0.0),
        stations=station_stats,
    )
