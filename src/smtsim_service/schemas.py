"""Request and response models.

**The configuration schema is defined exactly once, in `smtsim.config`.** These
models take the config as an opaque JSON object and validate it by building the
real dataclasses with `line_config_from_dict`. A hand-written Pydantic mirror of
`LineConfig` would be a second definition of the same thing -- four distribution
kinds, capacities, failure blocks, buffer rules -- and the two would drift. The
existing validators are also the ones with the right error messages: a buffer of
zero is rejected here by the same code that rejects it in the CLI, with the same
wording.

The cost is that OpenAPI shows `config` as a generic object rather than a typed
schema. That is paid for with a worked example generated from `DEFAULT_LINE`, so
/docs still shows a complete, valid, copy-pasteable request body -- and the
example cannot go stale, because it is derived rather than written down.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from smtsim.config import (
    BASELINE_LINE,
    Constant,
    Distribution,
    LineConfig,
    Triangular,
    line_config_from_dict,
)

# The worked example the API advertises, and what the web UI prefills its
# new-run form from. BASELINE_LINE rather than DEFAULT_LINE on purpose: the
# default line has no conveyors and no breakdowns, so a run submitted from the
# example would never block and never fail -- which are the two things the
# replay UI exists to show.
CONFIG_EXAMPLE = BASELINE_LINE.to_dict()


def arrivals_advance_time(interarrival: Distribution) -> bool:
    """Whether the arrival process can ever move the simulation clock forward.

    An interarrival distribution that can only produce zero -- `constant` with
    seconds 0, or a triangular collapsed at 0 -- makes the source process spin
    forever scheduling zero-length timeouts at t=0. Simulated time never
    advances, so the horizon is never reached and the run never ends.

    On the command line that costs you a terminal. Through the API it
    permanently consumes a worker thread and leaves a run in 'running' with no
    way back, which makes it a denial of service on a public endpoint. The
    general problem is undecidable, but this is the reachable case and it is
    cheap to refuse. See the README on why a process pool would change the
    calculus here.
    """
    if isinstance(interarrival, Constant):
        return interarrival.seconds > 0
    if isinstance(interarrival, Triangular):
        return interarrival.high > 0
    return True


def validate_config(value: Any) -> dict[str, Any]:
    """Round-trip a config through the real dataclasses.

    Raises ValueError, which FastAPI turns into a 422 naming the offending
    field and carrying the validator's own message.
    """
    if not isinstance(value, dict):
        raise ValueError("config must be a JSON object")
    try:
        config = line_config_from_dict(value)
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(str(exc)) from None
    if not arrivals_advance_time(config.arrivals.interarrival):
        raise ValueError(
            "arrivals.interarrival must be able to produce a positive time; a "
            "distribution fixed at zero would never advance the simulation clock"
        )
    return value


def build_config(value: dict[str, Any]) -> LineConfig:
    return line_config_from_dict(value)


class RunRequest(BaseModel):
    config: dict[str, Any] = Field(
        default_factory=lambda: dict(CONFIG_EXAMPLE),
        description="A line configuration, in the same shape as a .toml config file.",
        json_schema_extra={"example": CONFIG_EXAMPLE},
    )
    minutes: float = Field(default=480.0, gt=0, description="Length of the simulated shift.")
    seed: int = Field(
        default=42, ge=0, description="Master seed. The same seed reproduces the run."
    )
    warmup_minutes: float = Field(
        default=0.0, ge=0, description="Opening minutes excluded from the metrics."
    )
    store_events: bool = Field(
        default=True,
        description="Persist the event log. Turn off to keep only the summary.",
    )

    @field_validator("config")
    @classmethod
    def _check_config(cls, value: Any) -> dict[str, Any]:
        return validate_config(value)

    def line_config(self) -> LineConfig:
        return build_config(self.config).with_seed(self.seed)


class ComparisonRequest(BaseModel):
    baseline: dict[str, Any] = Field(
        default_factory=lambda: dict(CONFIG_EXAMPLE),
        json_schema_extra={"example": CONFIG_EXAMPLE},
    )
    variant: dict[str, Any] = Field(
        default_factory=lambda: dict(CONFIG_EXAMPLE),
        json_schema_extra={"example": CONFIG_EXAMPLE},
    )
    seeds: int = Field(default=30, ge=2, description="How many seeds each side is run under.")
    minutes: float = Field(default=480.0, gt=0)
    warmup_minutes: float = Field(default=0.0, ge=0)
    store_events: bool = Field(
        default=False,
        description=(
            "Persist every constituent run's events. Off by default: a 30-seed "
            "comparison is 60 runs and roughly 400,000 events."
        ),
    )

    @field_validator("baseline", "variant")
    @classmethod
    def _check_config(cls, value: Any) -> dict[str, Any]:
        return validate_config(value)


class RunSummary(BaseModel):
    id: UUID
    status: str
    seed: int
    minutes: float
    warmup_minutes: float
    event_count: int
    stores_events: bool
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RunDetail(RunSummary):
    config: dict[str, Any]
    summary: dict[str, Any] | None = None


class RunPage(BaseModel):
    items: list[RunSummary]
    total: int
    limit: int
    offset: int


class EventRecord(BaseModel):
    seq: int
    t: float
    event: str
    board: int | None = None
    station: str | None = None
    detail: dict[str, Any] | None = None


class EventPage(BaseModel):
    run_id: UUID
    items: list[EventRecord]
    next_after: int | None = Field(
        default=None, description="Pass as ?after= to fetch the next page. Null at the end."
    )
    limit: int


class ComparisonRunLink(BaseModel):
    run_id: UUID
    role: str
    seed: int
    status: str


class ComparisonDetail(BaseModel):
    id: UUID
    status: str
    seeds: int
    minutes: float
    warmup_minutes: float
    baseline_config: dict[str, Any]
    variant_config: dict[str, Any]
    summary: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    runs: list[ComparisonRunLink] = Field(default_factory=list)


class Health(BaseModel):
    status: str
    database: bool
    version: str
    in_flight: int


class Accepted(BaseModel):
    id: UUID
    status: str
