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
class StationConfig:
    """One machine in the line.

    ``capacity`` is the number of boards the station can hold at once: 1 for the
    single-board machines, more for the reflow oven tunnel.
    """

    name: str
    service_time: Distribution
    capacity: int = 1

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("station name must not be empty")
        if self.capacity < 1:
            raise ValueError(f"station {self.name!r} capacity must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "capacity": self.capacity,
            "service_time": self.service_time.to_dict(),
        }


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

    # --- EXTENSION POINT: machine breakdowns -------------------------------
    # Stage 2 adds a `failures: FailureConfig | None` field here, giving each
    # station an MTBF/MTTR pair. The station process gains a preemptive repair
    # process; nothing else in this module changes. See README "Roadmap".
    # ----------------------------------------------------------------------

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
        )
        for spec in stations_spec
    )
    return LineConfig(
        name=data.get("name", "unnamed line"),
        arrivals=arrivals,
        stations=stations,
        seed=int(data.get("seed", DEFAULT_LINE.seed)),
    )


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
