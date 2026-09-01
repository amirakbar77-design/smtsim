"""Discrete-event simulation of an SMT PCB assembly line."""

from smtsim.compare import Comparison, MetricComparison, compare, seed_sequence
from smtsim.config import (
    DEFAULT_LINE,
    FailureConfig,
    LineConfig,
    StationConfig,
    load_line_config,
)
from smtsim.events import Event, EventSink, EventType, JsonlSink, ListSink, NullSink
from smtsim.line import Line, simulate
from smtsim.rng import RngStreams
from smtsim.stats import LineStats, PairedInterval, StationStats, paired_interval, summarise

__version__ = "0.2.0"

__all__ = [
    "DEFAULT_LINE",
    "Comparison",
    "Event",
    "EventSink",
    "EventType",
    "FailureConfig",
    "JsonlSink",
    "Line",
    "LineConfig",
    "LineStats",
    "ListSink",
    "MetricComparison",
    "NullSink",
    "PairedInterval",
    "RngStreams",
    "StationConfig",
    "StationStats",
    "compare",
    "load_line_config",
    "paired_interval",
    "seed_sequence",
    "simulate",
    "summarise",
]
