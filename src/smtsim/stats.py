"""Summary metrics computed from an event log.

Everything here is derived from the event stream alone, so `smtsim stats` on a
saved log and the table printed at the end of a run go through exactly the same
code. The simulation itself keeps no counters for reporting.

Metrics are integrated over a measurement window ``[warmup, horizon]``. Events
before the warm-up are still processed -- they establish the state of the line
at the moment the window opens -- but the time they occupy contributes nothing.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

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
    """Per-station metrics over the measurement window.

    The four time accounts -- working, blocked, starved, down -- partition the
    window exactly, per unit of capacity. Every slot-second of the measured
    window is in one of them and none is in two, so the four fractions sum to
    one. That identity is the sharpest structural check on the model: it holds
    only if the number of boards physically inside the station always equals
    the number being worked on plus the number stuck waiting to leave.
    """

    name: str
    capacity: int
    measured_slot_seconds: float
    working_slot_seconds: float
    blocked_slot_seconds: float
    starved_slot_seconds: float
    down_slot_seconds: float
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
    def uptime_slot_seconds(self) -> float:
        return max(0.0, self.measured_slot_seconds - self.down_slot_seconds)

    def _fraction(self, slot_seconds: float) -> float:
        if self.measured_slot_seconds <= 0:
            return 0.0
        return slot_seconds / self.measured_slot_seconds

    @property
    def working_fraction(self) -> float:
        """Producing: a board under the head, being worked on."""
        return self._fraction(self.working_slot_seconds)

    @property
    def blocked_fraction(self) -> float:
        """Finished a board and holding it, because downstream has no room."""
        return self._fraction(self.blocked_slot_seconds)

    @property
    def starved_fraction(self) -> float:
        """Free capacity with no board in it to work on."""
        return self._fraction(self.starved_slot_seconds)

    @property
    def down_fraction(self) -> float:
        """Under repair. The whole station, however many slots it has."""
        return self._fraction(self.down_slot_seconds)

    @property
    def accounted_slot_seconds(self) -> float:
        """The four accounts summed. Must equal ``measured_slot_seconds``."""
        return (
            self.working_slot_seconds
            + self.blocked_slot_seconds
            + self.starved_slot_seconds
            + self.down_slot_seconds
        )

    @property
    def utilisation(self) -> float:
        """Busy against the clock. The same thing as the working fraction."""
        return self.working_fraction

    @property
    def utilisation_uptime(self) -> float:
        """Busy against the time the station was not under repair."""
        if self.uptime_slot_seconds <= 0:
            return 0.0
        return self.working_slot_seconds / self.uptime_slot_seconds

    @property
    def availability(self) -> float:
        if self.measured_slot_seconds <= 0:
            return 1.0
        return self.uptime_slot_seconds / self.measured_slot_seconds

    @property
    def can_fail(self) -> bool:
        return self.failures > 0 or self.downtime_seconds > 0.0

    @property
    def can_block(self) -> bool:
        return self.blocked_slot_seconds > 0.0

    def to_dict(self) -> dict[str, Any]:
        """The whole station row, ready for JSON.

        Everything the terminal table shows is here, so a consumer that renders
        this -- the API, and stage 4's replay UI -- never has to recompute a
        metric or re-read an event log to draw the same picture.
        """
        return {
            "name": self.name,
            "capacity": self.capacity,
            "measured_slot_seconds": self.measured_slot_seconds,
            "working_slot_seconds": self.working_slot_seconds,
            "blocked_slot_seconds": self.blocked_slot_seconds,
            "starved_slot_seconds": self.starved_slot_seconds,
            "down_slot_seconds": self.down_slot_seconds,
            "working_fraction": self.working_fraction,
            "blocked_fraction": self.blocked_fraction,
            "starved_fraction": self.starved_fraction,
            "down_fraction": self.down_fraction,
            "utilisation": self.utilisation,
            "utilisation_uptime": self.utilisation_uptime,
            "availability": self.availability,
            "boards_started": self.boards_started,
            "boards_finished": self.boards_finished,
            "max_queue_length": self.max_queue_length,
            "mean_wait_seconds": self.mean_wait_seconds,
            "mean_service_seconds": self.mean_service_seconds,
            "failures": self.failures,
            "downtime_seconds": self.downtime_seconds,
            "observed_mtbf_seconds": self.observed_mtbf_seconds,
            "observed_mttr_seconds": self.observed_mttr_seconds,
        }


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

    def to_dict(self) -> dict[str, Any]:
        """The whole summary, ready for JSON."""
        bottleneck = self.bottleneck
        return {
            "line_name": self.line_name,
            "seed": self.seed,
            "horizon_seconds": self.horizon_seconds,
            "warmup_seconds": self.warmup_seconds,
            "window_seconds": self.window_seconds,
            "boards_arrived": self.boards_arrived,
            "boards_completed": self.boards_completed,
            "boards_in_system": self.boards_in_system,
            "throughput_per_hour": self.throughput_per_hour,
            "mean_cycle_time_seconds": self.mean_cycle_time_seconds,
            "p95_cycle_time_seconds": self.p95_cycle_time_seconds,
            "mean_cycle_time_minutes": self.mean_cycle_time_minutes,
            "p95_cycle_time_minutes": self.p95_cycle_time_minutes,
            "has_failures": self.has_failures,
            "bottleneck": None if bottleneck is None else bottleneck.name,
            "stations": [station.to_dict() for station in self.stations],
        }


@dataclass(slots=True)
class _StationAccumulator:
    """Running state for one station while the log is streamed through."""

    name: str
    order: int
    capacity: int = 1

    working: int = 0
    blocked: int = 0
    occupied: int = 0
    queue_length: int = 0
    failed: bool = False
    failed_since: float | None = None
    last_time: float = 0.0

    working_slot_seconds: float = 0.0
    blocked_slot_seconds: float = 0.0
    starved_slot_seconds: float = 0.0
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
        """Integrate the time-weighted quantities up to ``to``.

        A station under repair contributes its whole capacity to downtime: the
        machine is unavailable however many boards happen to be sitting in it.
        Otherwise each slot is working, blocked, or empty, and ``occupied`` is
        tracked from its own events rather than inferred from the other two --
        which is what gives the four-way identity something to catch.
        """
        span = _overlap(self.last_time, to, window_start, window_end)
        if span > 0.0:
            if self.failed:
                self.downtime_seconds += span
            else:
                self.working_slot_seconds += self.working * span
                self.blocked_slot_seconds += self.blocked * span
                self.starved_slot_seconds += (self.capacity - self.occupied) * span
                if self.working > 0:
                    self.operating_seconds += span
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
                station.occupied += 1
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
                station.occupied = max(0, station.occupied - 1)
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

            case EventType.TRANSFER_BLOCKED:
                station = accumulator(event.station or "")
                station.advance(event.time, warmup, horizon_seconds or last_time)
                station.blocked += 1
                station.occupied += 1

            case EventType.TRANSFER_UNBLOCKED:
                station = accumulator(event.station or "")
                station.advance(event.time, warmup, horizon_seconds or last_time)
                station.blocked = max(0, station.blocked - 1)
                station.occupied = max(0, station.occupied - 1)

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
    measured_slot_seconds = window_seconds * station.capacity

    return StationStats(
        name=station.name,
        capacity=station.capacity,
        measured_slot_seconds=measured_slot_seconds,
        working_slot_seconds=station.working_slot_seconds,
        blocked_slot_seconds=station.blocked_slot_seconds,
        starved_slot_seconds=station.starved_slot_seconds,
        down_slot_seconds=station.downtime_seconds * station.capacity,
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


# --------------------------------------------------------------------------
# Paired comparison statistics.
#
# `smtsim compare` runs two configurations across the same seeds. Thanks to the
# per-station random streams, seed k gives both runs the same randomness
# wherever they are identical, so the two results for a seed are paired rather
# than independent. Analysing the paired differences removes the seed-to-seed
# variation that both configurations share, which is why 30 seeds can resolve a
# difference that would need far more runs if the samples were treated as
# independent.
#
# A paired t interval is used rather than a bootstrap. Both were viable; the t
# interval wins here because it is exactly reproducible without a resampling
# RNG, and because every number it produces can be checked by hand against a
# published t table -- which is what the tests do. Its cost is the assumption
# that the *differences* are roughly normal, which is mild for a mean over a
# 480-minute shift by the central limit theorem, and would be the wrong
# assumption for something like a p95, where a bootstrap would be better.
#
# There is no scipy here, so the t quantile is computed from the regularised
# incomplete beta function.
# --------------------------------------------------------------------------


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean of an empty sequence is undefined")
    return sum(values) / len(values)


def sample_stdev(values: Sequence[float]) -> float:
    """Bessel-corrected standard deviation."""
    if len(values) < 2:
        raise ValueError("standard deviation needs at least two values")
    centre = mean(values)
    return math.sqrt(sum((value - centre) ** 2 for value in values) / (len(values) - 1))


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Lentz's algorithm for the continued fraction of the incomplete beta."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    result = d

    for m in range(1, 300):
        m2 = 2 * m
        numerator = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + numerator * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + numerator / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        result *= d * c

        numerator = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + numerator * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + numerator / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        step = d * c
        result *= step
        if abs(step - 1.0) < 1e-15:
            break

    return result


def regularised_incomplete_beta(a: float, b: float, x: float) -> float:
    """``I_x(a, b)``, the CDF of a Beta(a, b) distribution."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_prefix = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    prefix = math.exp(log_prefix)
    if x < (a + 1.0) / (a + b + 2.0):
        return prefix * _beta_continued_fraction(a, b, x) / a
    return 1.0 - prefix * _beta_continued_fraction(b, a, 1.0 - x) / b


def student_t_cdf(t: float, df: float) -> float:
    """P(T <= t) for Student's t with ``df`` degrees of freedom."""
    if df <= 0:
        raise ValueError("degrees of freedom must be positive")
    tail = 0.5 * regularised_incomplete_beta(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t > 0 else tail


def student_t_ppf(probability: float, df: float) -> float:
    """The inverse of :func:`student_t_cdf`, found by bisection."""
    if not 0.0 < probability < 1.0:
        raise ValueError("probability must be strictly between 0 and 1")
    low, high = -1.0e4, 1.0e4
    for _ in range(200):
        middle = 0.5 * (low + high)
        if student_t_cdf(middle, df) < probability:
            low = middle
        else:
            high = middle
        if high - low < 1e-12:
            break
    return 0.5 * (low + high)


@dataclass(frozen=True, slots=True)
class PairedInterval:
    """A confidence interval on the mean of a set of paired differences."""

    n: int
    mean_difference: float
    low: float
    high: float
    confidence: float
    standard_error: float

    @property
    def excludes_zero(self) -> bool:
        """Whether the interval lies wholly above or wholly below zero.

        An interval that excludes zero means the difference is unlikely to be
        an artefact of the sample of seeds. It says nothing at all about
        whether the difference is big enough to be worth acting on.
        """
        return self.low > 0.0 or self.high < 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "mean_difference": self.mean_difference,
            "low": self.low,
            "high": self.high,
            "confidence": self.confidence,
            "standard_error": self.standard_error,
            "excludes_zero": self.excludes_zero,
        }


def paired_interval(
    baseline: Sequence[float],
    variant: Sequence[float],
    confidence: float = 0.95,
) -> PairedInterval:
    """Confidence interval on the mean of ``variant - baseline``, pairwise."""
    if len(baseline) != len(variant):
        raise ValueError("paired samples must be the same length")
    if len(baseline) < 2:
        raise ValueError("a paired interval needs at least two pairs")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be strictly between 0 and 1")

    differences = [v - b for b, v in zip(baseline, variant, strict=True)]
    n = len(differences)
    centre = mean(differences)
    standard_error = sample_stdev(differences) / math.sqrt(n)
    critical = student_t_ppf(0.5 + confidence / 2.0, df=n - 1)
    margin = critical * standard_error

    return PairedInterval(
        n=n,
        mean_difference=centre,
        low=centre - margin,
        high=centre + margin,
        confidence=confidence,
        standard_error=standard_error,
    )
