"""Station processes.

A station is a SimPy resource plus a service-time distribution. Waiting boards
are held by the resource's own request queue, so queueing is a consequence of
capacity and timing rather than something this module implements.

Each station owns its own random stream rather than being handed one. A station
therefore cannot draw from another station's numbers even by accident, which is
what makes two configurations comparable under a shared master seed.
"""

from __future__ import annotations

import random
from collections.abc import Generator
from dataclasses import dataclass

import simpy

from smtsim.config import StationConfig
from smtsim.events import Event, EventSink, EventType
from smtsim.rng import RngStreams


@dataclass(slots=True)
class Station:
    """One machine, bound to the environment it runs in."""

    config: StationConfig
    resource: simpy.Resource
    service_rng: random.Random

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
        )

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def capacity(self) -> int:
        return self.config.capacity

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
            sink.emit(Event(env.now, EventType.SERVICE_STARTED, board_id, self.name))

            # --- EXTENSION POINT: machine breakdowns --------------------------
            # Stage 2 replaces the single timeout below with a loop that consumes
            # remaining work while an availability process interrupts it, then
            # waits for repair before resuming. Confining failures to this block
            # keeps the rest of the model unchanged. See README "Roadmap".
            # ------------------------------------------------------------------
            yield env.timeout(self.config.service_time.sample(self.service_rng))

            sink.emit(Event(env.now, EventType.SERVICE_FINISHED, board_id, self.name))
