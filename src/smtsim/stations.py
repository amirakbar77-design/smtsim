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
"""

from __future__ import annotations

import random
from collections.abc import Generator
from dataclasses import dataclass, field

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
    service_rng: random.Random
    failure_rng: random.Random
    failed: bool = False
    working: int = 0
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

    def start(self, env: simpy.Environment, sink: EventSink) -> None:
        """Launch the availability process, if this station can break down."""
        if self.can_fail:
            self._availability = env.process(self._wear_and_repair(env, sink))

    def visit(
        self,
        env: simpy.Environment,
        board_id: int,
        sink: EventSink,
    ) -> Generator[simpy.Event, None, None]:
        """Take one board through this station: queue, seize, serve, release."""
        sink.emit(Event(env.now, EventType.QUEUE_ENTERED, board_id, self.name))

        with self.resource.request() as request:
            yield request
            yield from self._wait_for_repair()

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
        interrupted the moment the station falls idle.
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
