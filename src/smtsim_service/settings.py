"""Configuration from the environment.

Every knob the service has is here, read from the environment with an
``SMTSIM_`` prefix. Nothing is read from a file the client controls and nothing
has a default that would be unsafe in production -- notably the database URL,
which has no default at all, so a misconfigured deployment fails at startup
rather than quietly connecting somewhere unintended.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMTSIM_", env_file=".env", extra="ignore")

    database_url: str = Field(
        ...,
        description="postgresql:// URL. No default: an unset one must fail loudly.",
    )

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # See the README, "Why threads and not processes".
    worker_threads: int = Field(default=2, ge=1, le=32)

    db_pool_min: int = Field(default=1, ge=1)
    db_pool_max: int = Field(default=8, ge=1)

    # WebSocket batching: flush on whichever of these comes first.
    ws_batch_size: int = Field(default=250, ge=1)
    ws_batch_interval_ms: int = Field(default=100, ge=1)
    ws_queue_maxsize: int = Field(default=64, ge=1)

    # Guard rails on what a client may ask for.
    max_minutes: float = Field(default=20160.0, gt=0)
    max_comparison_seeds: int = Field(default=200, ge=2)

    # How many events to accumulate before a COPY into run_events.
    event_batch_size: int = Field(default=2000, ge=1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
