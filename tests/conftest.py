"""Shared fixtures: a single 480-minute reference run used by several tests."""

from __future__ import annotations

import pytest

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
