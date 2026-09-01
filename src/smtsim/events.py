"""The event log: its schema, its sinks, and its reader.

This is the seam between the simulation core and the outside world. The core
emits :class:`Event` objects into an :class:`EventSink`; only the sinks defined
here know anything about files or JSON.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import IO, Any, Protocol, runtime_checkable


class EventType(StrEnum):
    """Every kind of line that can appear in the log."""

    RUN_STARTED = "run_started"
    BOARD_ARRIVED = "board_arrived"
    QUEUE_ENTERED = "queue_entered"
    SERVICE_STARTED = "service_started"
    SERVICE_FINISHED = "service_finished"
    BOARD_COMPLETED = "board_completed"
    RUN_FINISHED = "run_finished"


@dataclass(frozen=True, slots=True)
class Event:
    """One thing that happened at one instant of simulated time."""

    time: float
    type: EventType
    board_id: int | None = None
    station: str | None = None
    detail: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "t": self.time,
            "event": str(self.type),
            "board": self.board_id,
            "station": self.station,
        }
        if self.detail is not None:
            record["detail"] = dict(self.detail)
        return record

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> Event:
        return cls(
            time=float(record["t"]),
            type=EventType(record["event"]),
            board_id=record.get("board"),
            station=record.get("station"),
            detail=record.get("detail"),
        )


@runtime_checkable
class EventSink(Protocol):
    """Anything the simulation can hand its events to."""

    def emit(self, event: Event) -> None: ...


class NullSink:
    """Discards everything. The cheapest way to run the model."""

    def emit(self, event: Event) -> None:
        return None


@dataclass(slots=True)
class ListSink:
    """Keeps events in memory. Used by tests and, later, by the web layer."""

    events: list[Event] = field(default_factory=list)

    def emit(self, event: Event) -> None:
        self.events.append(event)

    def __len__(self) -> int:
        return len(self.events)

    def __iter__(self) -> Iterator[Event]:
        return iter(self.events)


@dataclass(slots=True)
class JsonlSink:
    """Appends one compact JSON object per event to an open text stream."""

    stream: IO[str]

    def emit(self, event: Event) -> None:
        json.dump(event.to_dict(), self.stream, separators=(",", ":"), sort_keys=False)
        self.stream.write("\n")


@dataclass(slots=True)
class FanOutSink:
    """Broadcasts each event to several sinks."""

    sinks: tuple[EventSink, ...]

    def emit(self, event: Event) -> None:
        for sink in self.sinks:
            sink.emit(event)


@contextmanager
def open_jsonl(path: str | Path) -> Iterator[JsonlSink]:
    """Open ``path`` for writing as a JSONL event log, creating parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        yield JsonlSink(stream)


def read_jsonl(path: str | Path) -> Iterator[Event]:
    """Stream events back out of a saved log."""
    with Path(path).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield Event.from_dict(json.loads(line))
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number}: malformed event log line: {exc}") from None


def write_jsonl(path: str | Path, events: Iterable[Event]) -> None:
    """Write a sequence of events to ``path``."""
    with open_jsonl(path) as sink:
        for event in events:
            sink.emit(event)
