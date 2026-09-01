"""Assembles stations into a line and runs the simulation.

Nothing in this module touches the filesystem, the terminal or the clock. It is
handed an event sink and an optional progress callback; everything else it needs
comes from the config object and the named random streams derived from its seed.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Generator
from dataclasses import dataclass

import simpy

from smtsim.config import DEFAULT_LINE, LineConfig
from smtsim.events import Event, EventSink, EventType, NullSink
from smtsim.rng import RngStreams
from smtsim.stations import Station

ProgressHook = Callable[[float], None]

DEFAULT_PROGRESS_INTERVAL = 60.0


@dataclass(slots=True)
class Line:
    """A configured line, ready to run inside its own SimPy environment."""

    config: LineConfig
    sink: EventSink
    env: simpy.Environment
    streams: RngStreams
    arrival_rng: random.Random
    stations: tuple[Station, ...]
    boards_arrived: int = 0
    boards_completed: int = 0

    @classmethod
    def build(cls, config: LineConfig, sink: EventSink | None = None) -> Line:
        env = simpy.Environment()
        streams = RngStreams(master_seed=config.seed)
        return cls(
            config=config,
            sink=sink if sink is not None else NullSink(),
            env=env,
            streams=streams,
            arrival_rng=streams.arrivals(),
            stations=tuple(Station.build(env, spec, streams) for spec in config.stations),
        )

    def run(
        self,
        until: float,
        warmup: float = 0.0,
        on_progress: ProgressHook | None = None,
        progress_interval: float = DEFAULT_PROGRESS_INTERVAL,
    ) -> None:
        """Simulate ``until`` seconds of line time.

        ``warmup`` does not change the simulation at all; it is recorded in the
        log so that whoever reads it later knows which opening stretch the
        analyst intended to discard. Excluding it is `stats.summarise`'s job.

        ``on_progress`` is called with the current simulation time roughly every
        ``progress_interval`` seconds of simulated time. It draws no random
        numbers and mutates no state, so a run with a hook produces the same log
        as a run without one.
        """
        if until <= 0:
            raise ValueError("simulation horizon must be positive")
        if not 0.0 <= warmup < until:
            raise ValueError("warmup must be non-negative and shorter than the horizon")

        self.sink.emit(
            Event(
                0.0,
                EventType.RUN_STARTED,
                detail={
                    "horizon_seconds": until,
                    "warmup_seconds": warmup,
                    "seed": self.config.seed,
                    "line": self.config.to_dict(),
                },
            )
        )

        for station in self.stations:
            station.start(self.env, self.sink)

        self.env.process(self._source())
        if on_progress is not None:
            self.env.process(self._progress(until, on_progress, progress_interval))

        self.env.run(until=until)

        if on_progress is not None:
            on_progress(until)

        self.sink.emit(
            Event(
                until,
                EventType.RUN_FINISHED,
                detail={
                    "horizon_seconds": until,
                    "boards_arrived": self.boards_arrived,
                    "boards_completed": self.boards_completed,
                },
            )
        )

    def _source(self) -> Generator[simpy.Event, None, None]:
        """Feed bare boards into the head of the line."""
        arrivals = self.config.arrivals
        while arrivals.limit is None or self.boards_arrived < arrivals.limit:
            yield self.env.timeout(arrivals.interarrival.sample(self.arrival_rng))
            self.boards_arrived += 1
            self.env.process(self._board(self.boards_arrived))

    def _board(self, board_id: int) -> Generator[simpy.Event, None, None]:
        """Walk one board through every station in order.

        The board carries its buffer slot from one station to the next: a
        station hands the board into the downstream conveyor before letting go
        of its own machine, which is what makes a full conveyor block the
        machine behind it.
        """
        self.sink.emit(Event(self.env.now, EventType.BOARD_ARRIVED, board_id))

        slot = yield from self.stations[0].enter_queue(self.env, board_id, self.sink, None)
        for index, station in enumerate(self.stations):
            downstream = self.stations[index + 1] if index + 1 < len(self.stations) else None
            slot = yield from station.visit(self.env, board_id, self.sink, slot, downstream)

        self.boards_completed += 1
        self.sink.emit(Event(self.env.now, EventType.BOARD_COMPLETED, board_id))

    def _progress(
        self,
        until: float,
        on_progress: ProgressHook,
        interval: float,
    ) -> Generator[simpy.Event, None, None]:
        while True:
            yield self.env.timeout(min(interval, until - self.env.now))
            on_progress(self.env.now)


def simulate(
    horizon_seconds: float,
    config: LineConfig = DEFAULT_LINE,
    sink: EventSink | None = None,
    seed: int | None = None,
    warmup_seconds: float = 0.0,
    on_progress: ProgressHook | None = None,
    progress_interval: float = DEFAULT_PROGRESS_INTERVAL,
) -> Line:
    """Build and run a line in one call, returning the finished :class:`Line`."""
    if seed is not None:
        config = config.with_seed(seed)
    line = Line.build(config, sink)
    line.run(
        horizon_seconds,
        warmup=warmup_seconds,
        on_progress=on_progress,
        progress_interval=progress_interval,
    )
    return line
