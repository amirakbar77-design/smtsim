"""Sinks that carry events out of a worker thread.

Both of these satisfy `smtsim.events.EventSink` -- one `emit` method -- and the
simulation cannot tell them apart from the JSONL sink the CLI uses. That is the
whole return on the I/O-free design: adding a database and a WebSocket to this
project required writing two classes here and changing nothing in the model.

Both run on the worker thread. Neither may block for long: the simulation calls
`emit` several thousand times per run and anything expensive here shows up
directly as run time.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

from smtsim.events import Event

if TYPE_CHECKING:
    from smtsim_service.db import Database

logger = logging.getLogger(__name__)


class DatabaseSink:
    """Buffers events on the worker thread and COPYs them in batches.

    Writing row by row would put a database round trip inside the simulation
    loop. Batching keeps the cost per event to a list append, and pays the
    round trip once per `batch_size` events.
    """

    def __init__(
        self,
        database: Database,
        run_id: UUID,
        batch_size: int = 2000,
    ) -> None:
        from smtsim_service import repository

        self._database = database
        self._repository = repository
        self._run_id = run_id
        self._batch_size = batch_size
        self._pending: list[Event] = []
        self._written = 0

    @property
    def written(self) -> int:
        return self._written

    def emit(self, event: Event) -> None:
        self._pending.append(event)
        if len(self._pending) >= self._batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._pending:
            return
        batch, self._pending = self._pending, []
        with self._database.connection() as connection:
            self._repository.copy_events(connection, self._run_id, batch, self._written + 1)
        self._written += len(batch)

    def __enter__(self) -> DatabaseSink:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.flush()


class LoopBridgeSink:
    """Hands events from the worker thread to the event loop, and nowhere else.

    `loop.call_soon_threadsafe` is the only supported way to touch an asyncio
    object from another thread. Everything the WebSocket layer sees arrives
    through this one call; there is no shared mutable state between the
    simulation thread and the event loop, no lock, and no queue of our own.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        deliver: Callable[[Event], None],
    ) -> None:
        self._loop = loop
        self._deliver = deliver

    def emit(self, event: Event) -> None:
        try:
            self._loop.call_soon_threadsafe(self._deliver, event)
        except RuntimeError:
            # The loop has closed -- the server is shutting down. The run keeps
            # going and keeps persisting; only the live stream is lost.
            logger.debug("event loop closed; dropping live event")
