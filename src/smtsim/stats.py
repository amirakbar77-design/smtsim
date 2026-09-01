"""Summary metrics computed from an event log.

Everything here is derived from the event stream alone, so `smtsim stats` on a
saved log and the table printed at the end of a run go through exactly the same
code. The simulation itself keeps no counters for reporting.

Metrics are integrated over a measurement window ``[warmup, horizon]``. Events
before the warm-up are still processed -- they establish the state of the line
at the moment the window opens -- but the time they occupy contributes nothing.
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


def _overlap(start: float, end: float, window_start: float, window_end: float) -> float:
    """Length of the part of ``[start, end]`` that falls inside the window."""
    return max(0.0, min(end, window_end) - max(start, window_start))


@dataclass(frozen=True, slots=True)
class StationStats:
    """Per-station metrics over the measurement window."""

    name: str
    capacity: int
    utilisation: float
    utilisation_uptime: float
    availability: float
    boards_started: int
    boards_finished: int
    max_queue_length: int
    mean_wait_seconds: float
    mean_service_seconds: float
    failures: int
    downtime_seconds: float
    observed_mtbf_seconds: float | None
    observed_mttr_seconds: float | None

    @property
    def can_fail(self) -> bool:
        return self.failures > 0 or self.downtime_seconds > 0.0


@dataclass(frozen=True, slots=True)
class LineStats:
    """Whole-line metrics over the measurement window."""

    line_name: str
    seed: int | None
    horizon_seconds: float
    warmup_seconds: float
    boards_arrived: int
    boards_completed: int
    boards_in_system: int
    throughput_per_hour: float
    mean_cycle_time_seconds: float
    p95_cycle_time_seconds: float
    stations: tuple[StationStats, ...]

    @property
    def window_seconds(self) -> float:
        return max(0.0, self.horizon_seconds - self.warmup_seconds)

    @property
    def horizon_minutes(self) -> float:
        return self.horizon_seconds / SECONDS_PER_MINUTE

    @property
    def warmup_minutes(self) -> float:
        return self.warmup_seconds / SECONDS_PER_MINUTE

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

    @property
    def has_failures(self) -> bool:
        return any(station.can_fail for station in self.stations)

    def station(self, name: str) -> StationStats:
        for station in self.stations:
            if station.name == name:
                return station
        raise KeyError(name)


@dataclass(slots=True)
class _StationAccumulator:
    """Running state for one station while the log is streamed through."""

    name: str
    order: int
    capacity: int = 1

    working: int = 0
    queue_length: int = 0
    failed: bool = False
    failed_since: float | None = None
    last_time: float = 0.0

    busy_slot_seconds: float = 0.0
    operating_seconds: float = 0.0
    downtime_seconds: float = 0.0

    boards_started: int = 0
    boards_finished: int = 0
    max_queue_length: int = 0
    wait_seconds: float = 0.0
    waits_counted: int = 0
    service_seconds: float = 0.0
    services_counted: int = 0
    failures: int = 0
    repair_seconds: list[float] = field(default_factory=list)

    queued_at: dict[int, float] = field(default_factory=dict)
    working_since: dict[int, float] = field(default_factory=dict)
    worked_seconds: dict[int, float] = field(default_factory=dict)

    def advance(self, to: float, window_start: float, window_end: float) -> None:
        """Integrate the time-weighted quantities up to ``to``."""
        span = _overlap(self.last_time, to, window_start, window_end)
        if span > 0.0:
            self.busy_slot_seconds += self.working * span
            if self.working > 0:
                self.operating_seconds += span
            if self.failed:
                self.downtime_seconds += span
            self.max_queue_length = max(self.max_queue_length, self.queue_length)
        self.last_time = max(self.last_time, to)


def summarise(events: Iterable[Event], warmup_seconds: float | None = None) -> LineStats:
    """Reduce an event stream to a :class:`LineStats`.

    ``warmup_seconds`` of ``None`` means "use whatever the log says the run was
    recorded with", so ``smtsim stats`` reproduces a run's own table by default.
    Pass ``0.0`` to measure the whole run regardless of what was recorded.

    The stream is consumed once and never held in memory in full, so this works
    on logs far larger than RAM.
    """
    stations: dict[str, _StationAccumulator] = {}
    arrival_times: dict[int, float] = {}
    cycle_times: list[float] = []
    total_arrived = 0
    total_completed = 0
    arrived_in_window = 0
    completed_in_window = 0
    line_name = "unknown line"
    seed: int | None = None
    horizon_seconds = 0.0
    horizon_declared = False
    warmup = 0.0 if warmup_seconds is None else warmup_seconds
    last_time = 0.0

    def accumulator(name: str) -> _StationAccumulator:
        existing = stations.get(name)
        if existing is None:
            existing = _StationAccumulator(name=name, order=len(stations))
            stations[name] = existing
        return existing

    for event in events:
        last_time = max(last_time, event.time)
        in_window = event.time >= warmup

        match event.type:
            case EventType.RUN_STARTED:
                detail = event.detail or {}
                seed = detail.get("seed")
                horizon = detail.get("horizon_seconds")
                if horizon is not None:
                    horizon_seconds = float(horizon)
                    horizon_declared = True
                if warmup_seconds is None:
                    warmup = float(detail.get("warmup_seconds") or 0.0)
                line = detail.get("line") or {}
                line_name = line.get("name", line_name)
                for spec in line.get("stations", []):
                    accumulator(spec["name"]).capacity = int(spec.get("capacity", 1))

            case EventType.BOARD_ARRIVED:
                total_arrived += 1
                if in_window:
                    arrived_in_window += 1
                if event.board_id is not None:
                    arrival_times[event.board_id] = event.time

            case EventType.QUEUE_ENTERED:
                station = accumulator(event.station or "")
                station.advance(event.time, warmup, horizon_seconds or last_time)
                station.queue_length += 1
                if in_window:
                    station.max_queue_length = max(
                        station.max_queue_length, station.queue_length
                    )
                if event.board_id is not None:
                    station.queued_at[event.board_id] = event.time

            case EventType.SERVICE_STARTED:
                station = accumulator(event.station or "")
                station.advance(event.time, warmup, horizon_seconds or last_time)
                station.queue_length = max(0, station.queue_length - 1)
                station.working += 1
                if in_window:
                    station.boards_started += 1
                if event.board_id is not None:
                    station.working_since[event.board_id] = event.time
                    station.worked_seconds[event.board_id] = 0.0
                    queued = station.queued_at.pop(event.board_id, None)
                    if queued is not None and in_window:
                        station.wait_seconds += event.time - queued
                        station.waits_counted += 1

            case EventType.SERVICE_INTERRUPTED:
                station = accumulator(event.station or "")
                station.advance(event.time, warmup, horizon_seconds or last_time)
                station.working = max(0, station.working - 1)
                if event.board_id is not None:
                    since = station.working_since.pop(event.board_id, None)
                    if since is not None:
                        station.worked_seconds[event.board_id] = (
                            station.worked_seconds.get(event.board_id, 0.0)
                            + event.time
                            - since
                        )

            case EventType.SERVICE_RESUMED:
                station = accumulator(event.station or "")
                station.advance(event.time, warmup, horizon_seconds or last_time)
                station.working += 1
                if event.board_id is not None:
                    station.working_since[event.board_id] = event.time

            case EventType.SERVICE_FINISHED:
                station = accumulator(event.station or "")
                station.advance(event.time, warmup, horizon_seconds or last_time)
                station.working = max(0, station.working - 1)
                if in_window:
                    station.boards_finished += 1
                if event.board_id is not None:
                    since = station.working_since.pop(event.board_id, None)
                    worked = station.worked_seconds.pop(event.board_id, 0.0)
                    if since is not None:
                        worked += event.time - since
                        if in_window:
                            station.service_seconds += worked
                            station.services_counted += 1

            case EventType.STATION_FAILED:
                station = accumulator(event.station or "")
                station.advance(event.time, warmup, horizon_seconds or last_time)
                station.failed = True
                station.failed_since = event.time
                if in_window:
                    station.failures += 1

            case EventType.STATION_REPAIRED:
                station = accumulator(event.station or "")
                station.advance(event.time, warmup, horizon_seconds or last_time)
                station.failed = False
                if station.failed_since is not None and station.failed_since >= warmup:
                    station.repair_seconds.append(event.time - station.failed_since)
                station.failed_since = None

            case EventType.BOARD_COMPLETED:
                total_completed += 1
                if in_window:
                    completed_in_window += 1
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
    window_seconds = max(0.0, horizon_seconds - warmup)

    for station in stations.values():
        station.advance(horizon_seconds, warmup, horizon_seconds)

    station_stats = tuple(
        _finish_station(station, window_seconds)
        for station in sorted(stations.values(), key=lambda item: item.order)
    )

    return LineStats(
        line_name=line_name,
        seed=seed,
        horizon_seconds=horizon_seconds,
        warmup_seconds=warmup,
        boards_arrived=arrived_in_window,
        boards_completed=completed_in_window,
        boards_in_system=total_arrived - total_completed,
        throughput_per_hour=(
            completed_in_window * SECONDS_PER_HOUR / window_seconds if window_seconds > 0 else 0.0
        ),
        mean_cycle_time_seconds=(sum(cycle_times) / len(cycle_times) if cycle_times else 0.0),
        p95_cycle_time_seconds=(percentile(list(cycle_times), 0.95) if cycle_times else 0.0),
        stations=station_stats,
    )


def _finish_station(station: _StationAccumulator, window_seconds: float) -> StationStats:
    uptime_seconds = max(0.0, window_seconds - station.downtime_seconds)
    slot_seconds = window_seconds * station.capacity
    uptime_slot_seconds = uptime_seconds * station.capacity

    return StationStats(
        name=station.name,
        capacity=station.capacity,
        utilisation=(station.busy_slot_seconds / slot_seconds if slot_seconds > 0 else 0.0),
        utilisation_uptime=(
            station.busy_slot_seconds / uptime_slot_seconds if uptime_slot_seconds > 0 else 0.0
        ),
        availability=(uptime_seconds / window_seconds if window_seconds > 0 else 1.0),
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
        failures=station.failures,
        downtime_seconds=station.downtime_seconds,
        observed_mtbf_seconds=(
            station.operating_seconds / station.failures if station.failures else None
        ),
        observed_mttr_seconds=(
            sum(station.repair_seconds) / len(station.repair_seconds)
            if station.repair_seconds
            else None
        ),
    )
