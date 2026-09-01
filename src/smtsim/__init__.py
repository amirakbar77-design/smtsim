"""Discrete-event simulation of an SMT PCB assembly line."""

from smtsim.config import DEFAULT_LINE, LineConfig, StationConfig, load_line_config
from smtsim.events import Event, EventSink, EventType, JsonlSink, ListSink, NullSink
from smtsim.line import Line, simulate
from smtsim.stats import LineStats, StationStats, summarise

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_LINE",
    "Event",
    "EventSink",
    "EventType",
    "JsonlSink",
    "Line",
    "LineConfig",
    "LineStats",
    "ListSink",
    "NullSink",
    "StationConfig",
    "StationStats",
    "load_line_config",
    "simulate",
    "summarise",
]
