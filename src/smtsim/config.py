"""Configuration objects for the SMT line simulation.

Time is measured in seconds everywhere inside the simulation. The CLI accepts
minutes and converts at the boundary; stats are reported in the units a process
engineer would expect (minutes for cycle time, boards per hour for throughput).
"""

from __future__ import annotations

import json
import math
import random
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

SECONDS_PER_MINUTE = 60.0
SECONDS_PER_HOUR = 3600.0


@runtime_checkable
class Distribution(Protocol):
    """A sampler for a duration in seconds, driven by an injected RNG."""

    def sample(self, rng: random.Random) -> float: ...

    def to_dict(self) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class Constant:
    """A fixed duration. Used for conveyor transit times."""

    seconds: float

    def __post_init__(self) -> None:
        if self.seconds < 0:
            raise ValueError("constant duration must be non-negative")

    def sample(self, rng: random.Random) -> float:
        return self.seconds

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "constant", "seconds": self.seconds}


@dataclass(frozen=True, slots=True)
class LogNormal:
    """Right-skewed durations parameterised by mean and coefficient of variation."""

    mean: float
    cv: float

    def __post_init__(self) -> None:
        if self.mean <= 0:
            raise ValueError("lognormal mean must be positive")
        if self.cv <= 0:
            raise ValueError("lognormal cv must be positive")

    def sample(self, rng: random.Random) -> float:
        sigma = math.sqrt(math.log1p(self.cv * self.cv))
        mu = math.log(self.mean) - 0.5 * sigma * sigma
        return rng.lognormvariate(mu, sigma)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "lognormal", "mean": self.mean, "cv": self.cv}


@dataclass(frozen=True, slots=True)
class Exponential:
    """Memoryless durations. Used for time between failures."""

    mean: float

    def __post_init__(self) -> None:
        if self.mean <= 0:
            raise ValueError("exponential mean must be positive")

    def sample(self, rng: random.Random) -> float:
        return rng.expovariate(1.0 / self.mean)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "exponential", "mean": self.mean}


@dataclass(frozen=True, slots=True)
class Triangular:
    """Bounded durations: optimistic, most likely, pessimistic."""

    low: float
    mode: float
    high: float

    def __post_init__(self) -> None:
        if not self.low <= self.mode <= self.high:
            raise ValueError("triangular requires low <= mode <= high")
        if self.low < 0:
            raise ValueError("triangular low must be non-negative")

    def sample(self, rng: random.Random) -> float:
        return rng.triangular(self.low, self.high, self.mode)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "triangular",
            "low": self.low,
            "mode": self.mode,
            "high": self.high,
        }


_DISTRIBUTION_TYPES: dict[str, type] = {
    "constant": Constant,
    "exponential": Exponential,
    "lognormal": LogNormal,
    "triangular": Triangular,
}


def distribution_from_dict(spec: dict[str, Any]) -> Distribution:
    """Build a distribution from a plain mapping such as a parsed config file."""
    data = dict(spec)
    kind = data.pop("kind", None)
    if kind is None:
        raise ValueError("distribution spec requires a 'kind' key")
    try:
        cls = _DISTRIBUTION_TYPES[kind]
    except KeyError:
        known = ", ".join(sorted(_DISTRIBUTION_TYPES))
        raise ValueError(f"unknown distribution kind {kind!r} (known: {known})") from None
    try:
        return cls(**data)
    except TypeError as exc:
        raise ValueError(f"invalid parameters for {kind} distribution: {exc}") from None


@dataclass(frozen=True, slots=True)
class FailureConfig:
    """How often a station breaks down, and for how long.

    ``mtbf`` is measured in **operating** seconds, not calendar seconds: the
    clock that counts down to the next failure only runs while the station is
    working on a board. A machine standing idle does not wear out. See the
    README for what to change if calendar-time failures are wanted instead.
    """

    mtbf: float
    mttr: float
    mttr_cv: float = 0.4

    def __post_init__(self) -> None:
        if self.mtbf <= 0:
            raise ValueError("mtbf must be positive")
        if self.mttr <= 0:
            raise ValueError("mttr must be positive")
        if self.mttr_cv <= 0:
            raise ValueError("mttr_cv must be positive")

    @property
    def time_to_failure(self) -> Distribution:
        """Exponential: a machine is no likelier to fail for having run a while."""
        return Exponential(mean=self.mtbf)

    @property
    def repair_time(self) -> Distribution:
        """Lognormal: most repairs are quick, a few drag on."""
        return LogNormal(mean=self.mttr, cv=self.mttr_cv)

    def to_dict(self) -> dict[str, Any]:
        return {"mtbf": self.mtbf, "mttr": self.mttr, "mttr_cv": self.mttr_cv}


@dataclass(frozen=True, slots=True)
class StationConfig:
    """One machine in the line.

    ``capacity`` is the number of boards the station can hold at once: 1 for the
    single-board machines, more for the reflow oven tunnel. ``failures`` is
    optional; a station without it never breaks down.

    ``input_buffer`` is the length of the conveyor feeding this station, in
    boards. A board occupies a buffer slot from the moment it joins the queue
    until the moment the machine starts work on it -- it is lifted off the
    conveyor and into the machine -- so a station can hold ``capacity`` boards
    on top of the ``input_buffer`` waiting for it. ``None`` means unbounded,
    which reproduces the behaviour of a line with no buffer modelling at all.
    """

    name: str
    service_time: Distribution
    capacity: int = 1
    failures: FailureConfig | None = None
    input_buffer: int | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("station name must not be empty")
        if self.capacity < 1:
            raise ValueError(f"station {self.name!r} capacity must be >= 1")
        if self.input_buffer is not None and self.input_buffer < 1:
            raise ValueError(
                f"station {self.name!r} input_buffer must be >= 1, or omitted for unbounded"
            )

    def to_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "name": self.name,
            "capacity": self.capacity,
            "service_time": self.service_time.to_dict(),
        }
        if self.failures is not None:
            record["failures"] = self.failures.to_dict()
        if self.input_buffer is not None:
            record["input_buffer"] = self.input_buffer
        return record


@dataclass(frozen=True, slots=True)
class ArrivalConfig:
    """How bare boards are fed into the head of the line."""

    interarrival: Distribution
    limit: int | None = None

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit < 0:
            raise ValueError("arrival limit must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {"interarrival": self.interarrival.to_dict(), "limit": self.limit}


@dataclass(frozen=True, slots=True)
class LineConfig:
    """A complete line: an arrival process and an ordered list of stations."""

    name: str
    arrivals: ArrivalConfig
    stations: tuple[StationConfig, ...]
    seed: int = 42

    def __post_init__(self) -> None:
        if not self.stations:
            raise ValueError("a line needs at least one station")
        names = [station.name for station in self.stations]
        if len(set(names)) != len(names):
            raise ValueError("station names must be unique")

    def with_seed(self, seed: int) -> LineConfig:
        return replace(self, seed=seed)

    def station(self, name: str) -> StationConfig:
        for station in self.stations:
            if station.name == name:
                return station
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "seed": self.seed,
            "arrivals": self.arrivals.to_dict(),
            "stations": [station.to_dict() for station in self.stations],
        }


DEFAULT_LINE = LineConfig(
    name="SMT line 1",
    seed=42,
    arrivals=ArrivalConfig(interarrival=LogNormal(mean=57.0, cv=0.45)),
    stations=(
        StationConfig(
            name="solder_paste_printer",
            service_time=LogNormal(mean=25.0, cv=0.15),
        ),
        StationConfig(
            name="pick_and_place",
            service_time=LogNormal(mean=52.0, cv=0.20),
        ),
        StationConfig(
            name="spi",
            service_time=Triangular(low=14.0, mode=17.0, high=26.0),
        ),
        StationConfig(
            name="reflow_oven",
            service_time=Constant(seconds=240.0),
            capacity=6,
        ),
    ),
)


# The line this project is actually about: the default machines, plus the
# conveyors between them and the reliability figures they run at. Everything the
# README quotes -- the station table, both what-if comparisons, the demos -- is
# this line, and it is what the API advertises as its worked example.
#
# Written as the *difference* from DEFAULT_LINE rather than transcribed, so the
# machines themselves are still defined once. It duplicates the buffer and
# failure numbers in configs/baseline.toml, which a test pins: see
# tests/test_config.py::test_the_baseline_constant_matches_its_config_file.
#
# It is a constant rather than a `load_line_config("configs/baseline.toml")` at
# import time because a library module should not read a file whose path depends
# on the caller's working directory, and should not fail to import when it is
# missing.
BASELINE_LINE = replace(
    DEFAULT_LINE,
    stations=(
        replace(
            DEFAULT_LINE.station("solder_paste_printer"),
            failures=FailureConfig(mtbf=7200.0, mttr=240.0, mttr_cv=0.5),
        ),
        replace(
            DEFAULT_LINE.station("pick_and_place"),
            input_buffer=3,
            failures=FailureConfig(mtbf=5400.0, mttr=420.0, mttr_cv=0.6),
        ),
        replace(DEFAULT_LINE.station("spi"), input_buffer=3),
        replace(
            DEFAULT_LINE.station("reflow_oven"),
            input_buffer=3,
            failures=FailureConfig(mtbf=28800.0, mttr=900.0, mttr_cv=0.4),
        ),
    ),
)


def line_config_from_dict(data: dict[str, Any]) -> LineConfig:
    """Build a :class:`LineConfig` from a parsed config document."""
    try:
        arrivals_spec = data["arrivals"]
        stations_spec = data["stations"]
    except KeyError as exc:
        raise ValueError(f"config is missing required key {exc.args[0]!r}") from None

    arrivals = ArrivalConfig(
        interarrival=distribution_from_dict(arrivals_spec["interarrival"]),
        limit=arrivals_spec.get("limit"),
    )
    stations = tuple(
        StationConfig(
            name=spec["name"],
            service_time=distribution_from_dict(spec["service_time"]),
            capacity=int(spec.get("capacity", 1)),
            failures=failure_config_from_dict(spec.get("failures")),
            input_buffer=(
                None if spec.get("input_buffer") is None else int(spec["input_buffer"])
            ),
        )
        for spec in stations_spec
    )
    return LineConfig(
        name=data.get("name", "unnamed line"),
        arrivals=arrivals,
        stations=stations,
        seed=int(data.get("seed", DEFAULT_LINE.seed)),
    )


def failure_config_from_dict(spec: dict[str, Any] | None) -> FailureConfig | None:
    """Build a :class:`FailureConfig`, or ``None`` for a station that never fails."""
    if spec is None:
        return None
    try:
        return FailureConfig(**spec)
    except TypeError as exc:
        raise ValueError(f"invalid failures block: {exc}") from None


def _parse_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml
    except ModuleNotFoundError:
        raise ValueError(
            "YAML config requires the optional dependency PyYAML. "
            "Install it with `uv sync --extra yaml`, or use a .toml/.json config, "
            "which needs no extra dependencies."
        ) from None
    return yaml.safe_load(text)


def load_line_config(path: str | Path) -> LineConfig:
    """Load a line configuration from a .toml, .yaml/.yml or .json file."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".toml":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    elif suffix in {".yaml", ".yml"}:
        data = _parse_yaml(path.read_text(encoding="utf-8"))
    elif suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise ValueError(
            f"unsupported config format {suffix!r}; expected .toml, .yaml, .yml or .json"
        )
    if not isinstance(data, dict):
        raise ValueError(f"config at {path} must contain a mapping at the top level")
    return line_config_from_dict(data)
