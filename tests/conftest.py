"""Shared fixtures.

The simulation fixtures are at the top; the service fixtures below them build a
migrated database and an app with its real lifespan. Nothing here imports a
service dependency at module scope, so a machine with no database still
collects and runs the simulation suite.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from service_fixtures import DATABASE_URL, run_alembic
from smtsim.config import SECONDS_PER_MINUTE
from smtsim.events import ListSink
from smtsim.line import simulate

SHIFT_SECONDS = 480 * SECONDS_PER_MINUTE


def run_events(seed: int = 42, minutes: float = 480.0) -> list:
    sink = ListSink()
    simulate(minutes * SECONDS_PER_MINUTE, sink=sink, seed=seed)
    return sink.events


@pytest.fixture(scope="session")
def shift_events() -> list:
    return run_events()


# --- service fixtures -------------------------------------------------------

@pytest.fixture(scope="session")
def migrated_database() -> Iterator[str]:
    """A database at head, rebuilt once for the session."""
    assert DATABASE_URL
    down = run_alembic("downgrade", "base")
    assert down.returncode == 0, down.stderr
    up = run_alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    yield DATABASE_URL


@pytest.fixture
def settings(migrated_database: str):
    from smtsim_service.settings import Settings

    return Settings(
        database_url=migrated_database,
        worker_threads=2,
        ws_batch_size=200,
        ws_batch_interval_ms=20,
        # Generous on purpose: the live-streaming tests use a 5,000-minute run,
        # which the simulation produces far faster than a socket drains it. The
        # disconnect-on-overflow policy is tested deliberately and separately,
        # with a queue sized to trigger it.
        ws_queue_maxsize=200_000,
        event_batch_size=500,
    )


@pytest.fixture
def clean_tables(migrated_database: str) -> Iterator[None]:
    import psycopg

    with psycopg.connect(migrated_database, autocommit=True) as connection:
        connection.execute("TRUNCATE comparison_runs, comparisons, run_events, runs CASCADE")
    yield


@pytest.fixture
def client(settings, clean_tables) -> Iterator:
    """A TestClient over the real app, with the real lifespan run.

    Starlette's TestClient is an httpx client; entering it as a context manager
    starts the app's lifespan, which is what opens the connection pool and the
    worker thread pool.
    """
    from starlette.testclient import TestClient

    from smtsim_service.app import create_app

    with TestClient(create_app(settings)) as test_client:
        yield test_client
