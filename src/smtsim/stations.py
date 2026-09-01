"""Station processes.

A station is a SimPy resource plus a service-time distribution. Waiting boards
are held by the resource's own request queue, so queueing is a consequence of
capacity and timing rather than something this module implements.

Each station owns its own random streams rather than being handed one. A station
therefore cannot draw from another station's numbers even by accident, which is
what makes two configurations comparable under a shared master seed.

Breakdowns live entirely in this module. A station with no ``failures`` block
starts no availability process, schedules no events and behaves exactly as it
did before breakdowns existed.

So do finite buffers. A station's input buffer is the conveyor feeding it; a
board holds a slot on that conveyor from the moment it joins the queue until the
machine lifts it off. When the *downstream* conveyor is full, a station that has
finished a board cannot put it down, so it holds on to both the board and its
own machine and does no further work: it blocks. A station whose ``input_buffer``
is ``None`` takes no part in any of this -- no request, no yield, no event -- so
a line configured without buffers follows exactly the code path it followed
before they existed.
"""

from __future__ import annotations

import random
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

import simpy

from smtsim.config import StationConfig
from smtsim.events import Event, EventSink, EventType
from smtsim.rng import RngStreams

BROKE_DOWN = "broke_down"
WENT_IDLE = "went_idle"


@dataclass(slots=True)
class Station:
    """One machine, bound to the environment it runs in."""

    config: StationConfig
    resource: simpy.Resource
    input_buffer: simpy.Resource | None
    service_rng: random.Random
    failure_rng: random.Random
    failed: bool = False
    working: int = 0
    blocked: int = 0
    _availability: simpy.Process | None = None
    _wearing: bool = False
    _repaired: simpy.Event | None = None
    _resumed_work: simpy.Event | None = None
    _in_service: dict[int, simpy.Process] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        env: simpy.Environment,
        config: StationConfig,
        streams: RngStreams,
    ) -> Station:
        return cls(
            config=config,
            resource=simpy.Resource(env, capacity=config.capacity),
            input_buffer=(
                None
                if config.input_buffer is None
                else simpy.Resource(env, capacity=config.input_buffer)
            ),
            service_rng=streams.station_service(config.name),
            failure_rng=streams.station_failures(config.name),
        )

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def capacity(self) -> int:
        return self.config.capacity

    @property
    def can_fail(self) -> bool:
        return self.config.failures is not None

    @property
    def can_block(self) -> bool:
        """Whether boards can ever be held up on their way *into* this station."""
        return self.input_buffer is not None

    def start(self, env: simpy.Environment, sink: EventSink) -> None:
        """Launch the availability process, if this station can break down."""
        if self.can_fail:
            self._availability = env.process(self._wear_and_repair(env, sink))

    def enter_queue(
        self,
        env: simpy.Environment,
        board_id: int,
        sink: EventSink,
        upstream: Station | None,
    ) -> Generator[simpy.Event, None, Any]:
        """Put a board onto the conveyor feeding this station.

        Called by whoever is holding the board: the line's source for the first
        station, or the upstream station once it has finished its own work. If
        no slot is free the board cannot move, and ``upstream`` -- the station
        still holding it -- is blocked for as long as that lasts. The first
        station has no ``upstream``, so boards simply wait to be loaded.

        Returns the buffer slot the board now holds, to be released when the
        machine takes it.
        """
        if self.input_buffer is None:
            sink.emit(Event(env.now, EventType.QUEUE_ENTERED, board_id, self.name))
            return None

        slot = self.input_buffer.request()
        blocking = upstream is not None and not slot.triggered

        if blocking:
            upstream._begin_block(board_id)
            sink.emit(Event(env.now, EventType.TRANSFER_BLOCKED, board_id, upstream.name))

        yield slot

        if blocking:
            sink.emit(Event(env.now, EventType.TRANSFER_UNBLOCKED, board_id, upstream.name))
            upstream._end_block(board_id)

        sink.emit(Event(env.now, EventType.QUEUE_ENTERED, board_id, self.name))
        return slot

    def visit(
        self,
        env: simpy.Environment,
        board_id: int,
        sink: EventSink,
        slot: Any,
        downstream: Station | None,
    ) -> Generator[simpy.Event, None, Any]:
        """Serve one board, then hand it downstream.

        ``slot`` is this station's buffer slot, already held by the board. The
        return value is the slot it holds in the *next* station's buffer, or
        ``None`` for the last station, whose boards leave the line and which
        therefore can never block.
        """
        with self.resource.request() as request:
            yield request
            yield from self._wait_for_repair()

            self._release_slot(slot)
            remaining = self.config.service_time.sample(self.service_rng)
            sink.emit(Event(env.now, EventType.SERVICE_STARTED, board_id, self.name))

            # --- EXTENSION POINT REALISED: machine breakdowns -----------------
            # Stage 1 had a single `yield env.timeout(...)` here. It is now a
            # loop, because a failure interrupts work in progress and the work
            # already done must survive: a board 40 s into a 52 s placement
            # resumes with 12 s left rather than starting over. Everything the
            # feature needs is still confined to this block and to the
            # availability process below.
            # ------------------------------------------------------------------
            while True:
                self._begin_work(env, board_id)
                started = env.now
                try:
                    yield env.timeout(remaining)
                    remaining = 0.0
                except simpy.Interrupt:
                    remaining -= env.now - started
                self._end_work(board_id)

                if remaining <= 0.0:
                    break

                sink.emit(Event(env.now, EventType.SERVICE_INTERRUPTED, board_id, self.name))
                yield from self._wait_for_repair()
                sink.emit(Event(env.now, EventType.SERVICE_RESUMED, board_id, self.name))

            sink.emit(Event(env.now, EventType.SERVICE_FINISHED, board_id, self.name))

            # Blocking after service: the machine is not free until the board it
            # just finished has somewhere to go. Holding the request across this
            # wait is the whole mechanism -- nothing else needs to know.
            next_slot = None
            if downstream is not None:
                next_slot = yield from downstream.enter_queue(env, board_id, sink, self)

        return next_slot

    def _release_slot(self, slot: Any) -> None:
        """The machine lifts the board off the conveyor, freeing its slot."""
        if slot is not None and self.input_buffer is not None:
            self.input_buffer.release(slot)

    def _begin_block(self, board_id: int) -> None:
        self.blocked += 1

    def _end_block(self, board_id: int) -> None:
        self.blocked -= 1

    def _begin_work(self, env: simpy.Environment, board_id: int) -> None:
        """Note that this board is actually being worked on, not merely holding a slot."""
        self._in_service[board_id] = env.active_process
        self.working += 1
        if self.working == 1 and self._resumed_work is not None:
            resumed, self._resumed_work = self._resumed_work, None
            resumed.succeed()

    def _end_work(self, board_id: int) -> None:
        self._in_service.pop(board_id, None)
        self.working -= 1
        if self.working == 0 and self._wearing and self._availability is not None:
            self._availability.interrupt(WENT_IDLE)

    def _wait_for_repair(self) -> Generator[simpy.Event, None, None]:
        while self.failed and self._repaired is not None:
            yield self._repaired

    def _wear_and_repair(
        self,
        env: simpy.Environment,
        sink: EventSink,
    ) -> Generator[simpy.Event, None, None]:
        """Count down to the next failure, then break, then repair, forever.

        The countdown consumes operating time rather than calendar time: it is
        suspended whenever the station has no board under the head, and it is
        interrupted the moment the station falls idle. A *blocked* station is
        idle by this definition -- ``_end_work`` has already run for the board
        it is holding -- so a machine stuck waiting for downstream space does
        not wear out either. That falls out of the existing mechanism rather
        than needing one of its own, which is worth a test rather than trust.
        """
        failures = self.config.failures
        assert failures is not None

        while True:
            remaining = failures.time_to_failure.sample(self.failure_rng)

            while remaining > 0.0:
                if self.working == 0:
                    yield self._wait_until_working(env)
                started = env.now
                self._wearing = True
                try:
                    yield env.timeout(remaining)
                    remaining = 0.0
                except simpy.Interrupt:
                    remaining = max(0.0, remaining - (env.now - started))
                finally:
                    self._wearing = False

            self.failed = True
            self._repaired = env.event()
            sink.emit(Event(env.now, EventType.STATION_FAILED, station=self.name))

            for process in list(self._in_service.values()):
                process.interrupt(BROKE_DOWN)

            yield env.timeout(failures.repair_time.sample(self.failure_rng))

            self.failed = False
            sink.emit(Event(env.now, EventType.STATION_REPAIRED, station=self.name))
            repaired, self._repaired = self._repaired, None
            repaired.succeed()

    def _wait_until_working(self, env: simpy.Environment) -> simpy.Event:
        if self._resumed_work is None:
            self._resumed_work = env.event()
        return self._resumed_work
