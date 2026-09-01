"""Helpers for the API tests.

The fixtures themselves live in conftest.py so pytest finds them by name;
what is left here is the database URL, the skip marker and two helpers.

These need a real Postgres -- not a mock and not SQLite, because the schema uses
jsonb, enums and COPY, none of which SQLite would exercise. The URL comes from
SMTSIM_TEST_DATABASE_URL and the whole service suite skips with a clear message
when it is unset, so the simulation tests still run on a machine with no
database at all.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

DATABASE_URL = os.environ.get("SMTSIM_TEST_DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parent.parent

requires_database = pytest.mark.skipif(
    not DATABASE_URL,
    reason=(
        "SMTSIM_TEST_DATABASE_URL is not set. Start a Postgres and export it:\n"
        "  docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=test -e POSTGRES_USER=smtsim"
        " -e POSTGRES_DB=smtsim_test postgres:17-alpine\n"
        "  export SMTSIM_TEST_DATABASE_URL="
        "postgresql://smtsim:test@localhost:55432/smtsim_test"
    ),
)


def run_alembic(*args: str, url: str | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, SMTSIM_DATABASE_URL=url or DATABASE_URL or "")
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def wait_for_status(client, url: str, target: set[str], timeout: float = 30.0) -> dict:
    """Poll a job until it reaches one of ``target``. Jobs are asynchronous by design."""
    import time

    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        response = client.get(url)
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in target:
            return body
        time.sleep(0.02)
    raise AssertionError(f"{url} stuck in {body.get('status')!r} after {timeout}s")
