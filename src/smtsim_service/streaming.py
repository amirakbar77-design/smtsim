"""Live event streaming to WebSocket clients.

A 480-minute simulation emits its whole log in about a tenth of a second. Sent
one frame per event that is seven thousand frames in 100 ms, which no browser
will absorb and no network wants. Events are therefore batched: a frame is
flushed when `batch_size` events have accumulated or `batch_interval_ms` has
passed, whichever comes first.

**When a client falls behind it is disconnected, not degraded.** Each subscriber
has a bounded queue of events; if it fills, the subscriber is closed with
WebSocket code 1013 and told to fall back to the paginated REST endpoint.
Dropping events would be the other option and is the wrong one here: every event
in this stream mutates the state of the line, so a consumer that misses a
`service_finished` shows a board stuck at a station forever. A silently wrong
picture is worse than a visible disconnect, and the lossless replay is one HTTP
call away.

Worth being plain about the consequence: the simulation is not a real-time
source. It produces a 480-minute shift in about a tenth of a second, far faster
than any socket will carry it, so the queue is what absorbs the difference. Long
runs can outrun any finite queue, and such a client will be disconnected and
should replay instead. Live streaming is for watching a run in progress, not for
guaranteeing delivery; the REST endpoint is the lossless path.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from smtsim.events import Event

logger = logging.getLogger(__name__)

STREAM_CLOSED = object()


class SlowConsumer(Exception):
    """Raised when a subscriber's queue overflows."""


@dataclass(eq=False)
class Subscriber:
    """One WebSocket's view of a run. ``eq=False`` so it hashes by identity and
    can live in a set: two subscribers with equal queues are still two clients."""

    queue: asyncio.Queue[Any]
    dropped: bool = False


@dataclass
class RunChannel:
    """The set of subscribers listening to one run."""

    subscribers: set[Subscriber] = field(default_factory=set)
    finished: bool = False


class EventBroker:
    """Fans live events out to whoever is listening, per run.

    Every method here runs on the event loop. The only thing that crosses from
    a worker thread is `publish`, and it arrives via
    `loop.call_soon_threadsafe` from `LoopBridgeSink`.
    """

    def __init__(self, queue_maxsize: int = 64) -> None:
        self._channels: dict[UUID, RunChannel] = {}
        self._queue_maxsize = queue_maxsize

    def has_listeners(self, run_id: UUID) -> bool:
        channel = self._channels.get(run_id)
        return bool(channel and channel.subscribers)

    def publish(self, run_id: UUID, event: Event) -> None:
        channel = self._channels.get(run_id)
        if channel is None:
            return
        for subscriber in list(channel.subscribers):
            self._offer(channel, subscriber, event.to_dict())

    def close(self, run_id: UUID, status: str, error: str | None = None) -> None:
        """Tell every listener the run has ended, then retire the channel."""
        channel = self._channels.get(run_id)
        if channel is None:
            return
        channel.finished = True
        for subscriber in list(channel.subscribers):
            self._offer(channel, subscriber, {"status": status, "error": error}, sentinel=True)
        if not channel.subscribers:
            self._channels.pop(run_id, None)

    def _offer(
        self,
        channel: RunChannel,
        subscriber: Subscriber,
        payload: Any,
        sentinel: bool = False,
    ) -> None:
        item = (STREAM_CLOSED, payload) if sentinel else payload
        try:
            subscriber.queue.put_nowait(item)
        except asyncio.QueueFull:
            # See the module docstring: a slow consumer is disconnected rather
            # than silently given an incomplete stream.
            subscriber.dropped = True
            channel.subscribers.discard(subscriber)
            logger.warning("websocket subscriber fell behind; disconnecting")

    @contextmanager
    def subscribe(self, run_id: UUID) -> Iterator[Subscriber]:
        channel = self._channels.setdefault(run_id, RunChannel())
        subscriber = Subscriber(queue=asyncio.Queue(maxsize=self._queue_maxsize))
        channel.subscribers.add(subscriber)
        try:
            yield subscriber
        finally:
            channel.subscribers.discard(subscriber)
            if not channel.subscribers and channel.finished:
                self._channels.pop(run_id, None)

    def open_channel(self, run_id: UUID) -> None:
        """Create a channel before the run starts, so no early event is missed."""
        self._channels.setdefault(run_id, RunChannel())


async def next_batch(
    subscriber: Subscriber,
    batch_size: int,
    batch_interval_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Collect up to `batch_size` events, or whatever arrives within the interval.

    Returns the batch and, if the run ended during it, the closing frame.
    Blocks until at least one item is available so an idle stream costs nothing.
    """
    interval = batch_interval_ms / 1000.0
    batch: list[dict[str, Any]] = []
    closing: dict[str, Any] | None = None

    first = await subscriber.queue.get()
    if isinstance(first, tuple) and first[0] is STREAM_CLOSED:
        return batch, first[1]
    batch.append(first)

    deadline = asyncio.get_running_loop().time() + interval
    while len(batch) < batch_size:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            item = await asyncio.wait_for(subscriber.queue.get(), timeout=remaining)
        except TimeoutError:
            break
        if isinstance(item, tuple) and item[0] is STREAM_CLOSED:
            closing = item[1]
            break
        batch.append(item)

    return batch, closing
