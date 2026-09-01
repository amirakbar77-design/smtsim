"""Running simulations without blocking the event loop.

The simulation is synchronous and CPU-bound; FastAPI runs an asyncio loop.
Calling `Line.run` in a request handler would stall every other connection for
the duration, so runs are submitted to a thread pool and the request returns a
job id immediately.

Job state lives in Postgres, never in a dict on this object. A process-local
dict dies with the process, cannot be inspected while the service is running,
and is invisible to a second replica. The only in-memory state here is the set
of futures still in flight, kept so that shutdown can wait for them.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from uuid import UUID, uuid4

from smtsim.compare import BASELINE, VARIANT, compare, seed_sequence
from smtsim.config import SECONDS_PER_MINUTE, LineConfig
from smtsim.events import Event, EventSink, FanOutSink, ListSink
from smtsim.line import Line
from smtsim.stats import LineStats, summarise
from smtsim_service import repository
from smtsim_service.db import Database
from smtsim_service.settings import Settings
from smtsim_service.sinks import DatabaseSink, LoopBridgeSink
from smtsim_service.streaming import EventBroker

logger = logging.getLogger(__name__)


class JobRunner:
    """Submits simulations to a thread pool and records their outcome."""

    def __init__(self, database: Database, broker: EventBroker, settings: Settings) -> None:
        self._database = database
        self._broker = broker
        self._settings = settings
        self._executor = ThreadPoolExecutor(
            max_workers=settings.worker_threads, thread_name_prefix="smtsim-worker"
        )
        self._in_flight: set[Future] = set()

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    @property
    def in_flight(self) -> int:
        return len({future for future in self._in_flight if not future.done()})

    def _track(self, future: Future) -> None:
        self._in_flight.add(future)
        future.add_done_callback(self._in_flight.discard)

    # --- runs ---------------------------------------------------------------

    def submit_run(
        self,
        run_id: UUID,
        config: LineConfig,
        minutes: float,
        warmup_minutes: float,
        store_events: bool,
    ) -> None:
        loop = asyncio.get_running_loop()
        self._broker.open_channel(run_id)
        future = self._executor.submit(
            self._execute_run, loop, run_id, config, minutes, warmup_minutes, store_events
        )
        self._track(future)

    def _execute_run(
        self,
        loop: asyncio.AbstractEventLoop,
        run_id: UUID,
        config: LineConfig,
        minutes: float,
        warmup_minutes: float,
        store_events: bool,
    ) -> None:
        """Runs on a worker thread. Must never raise: a lost exception would
        leave the run stuck in 'running' with nothing to explain why."""
        try:
            with self._database.connection() as connection:
                repository.mark_run_started(connection, run_id)

            collector = ListSink()
            bridge = LoopBridgeSink(loop, partial(self._publish, run_id))
            sinks: list[EventSink] = [collector, bridge]

            database_sink: DatabaseSink | None = None
            if store_events:
                database_sink = DatabaseSink(
                    self._database, run_id, batch_size=self._settings.event_batch_size
                )
                sinks.append(database_sink)

            line = Line.build(config, FanOutSink(tuple(sinks)))
            line.run(
                minutes * SECONDS_PER_MINUTE,
                warmup=warmup_minutes * SECONDS_PER_MINUTE,
            )

            if database_sink is not None:
                database_sink.flush()

            stats = summarise(collector.events)
            with self._database.connection() as connection:
                repository.mark_run_succeeded(
                    connection, run_id, stats.to_dict(), len(collector.events)
                )
            self._close(loop, run_id, "succeeded")

        except BaseException as exc:  # noqa: BLE001 - the worker is the last line of defence
            self._fail_run(loop, run_id, exc)

    def _fail_run(
        self, loop: asyncio.AbstractEventLoop, run_id: UUID, exc: BaseException
    ) -> None:
        message = f"{type(exc).__name__}: {exc}"
        logger.error("run %s failed: %s", run_id, message)
        logger.debug("%s", traceback.format_exc())
        try:
            with self._database.connection() as connection:
                repository.mark_run_failed(connection, run_id, message)
        except Exception:
            logger.exception("could not record failure for run %s", run_id)
        self._close(loop, run_id, "failed", message)

    # --- comparisons --------------------------------------------------------

    def submit_comparison(
        self,
        comparison_id: UUID,
        baseline: LineConfig,
        variant: LineConfig,
        seeds: int,
        minutes: float,
        warmup_minutes: float,
        store_events: bool,
    ) -> None:
        future = self._executor.submit(
            self._execute_comparison,
            comparison_id,
            baseline,
            variant,
            seeds,
            minutes,
            warmup_minutes,
            store_events,
        )
        self._track(future)

    def _execute_comparison(
        self,
        comparison_id: UUID,
        baseline: LineConfig,
        variant: LineConfig,
        seeds: int,
        minutes: float,
        warmup_minutes: float,
        store_events: bool,
    ) -> None:
        try:
            with self._database.connection() as connection:
                repository.mark_comparison_started(connection, comparison_id)

            horizon = minutes * SECONDS_PER_MINUTE
            warmup = warmup_minutes * SECONDS_PER_MINUTE
            run_ids: dict[tuple[str, int], UUID] = {}
            sinks: dict[tuple[str, int], DatabaseSink] = {}

            def sink_factory(role: str, seed: int, config: LineConfig) -> EventSink | None:
                run_id = uuid4()
                run_ids[(role, seed)] = run_id
                with self._database.connection() as connection:
                    repository.create_run(
                        connection,
                        run_id,
                        seed=seed,
                        minutes=minutes,
                        warmup_minutes=warmup_minutes,
                        config=config.to_dict(),
                        stores_events=store_events,
                        status="running",
                    )
                    repository.link_comparison_run(connection, comparison_id, run_id, role, seed)
                if not store_events:
                    return None
                sink = DatabaseSink(
                    self._database, run_id, batch_size=self._settings.event_batch_size
                )
                sinks[(role, seed)] = sink
                return sink

            def on_stats(role: str, seed: int, config: LineConfig, stats: LineStats) -> None:
                key = (role, seed)
                run_id = run_ids[key]
                sink = sinks.pop(key, None)
                event_count = 0
                if sink is not None:
                    sink.flush()
                    event_count = sink.written
                with self._database.connection() as connection:
                    repository.mark_run_succeeded(
                        connection, run_id, stats.to_dict(), event_count
                    )

            result = compare(
                baseline,
                variant,
                seed_sequence(seeds),
                horizon,
                warmup_seconds=warmup,
                sink_factory=sink_factory,
                on_stats=on_stats,
            )

            with self._database.connection() as connection:
                repository.mark_comparison_succeeded(
                    connection, comparison_id, result.to_dict()
                )

        except BaseException as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"
            logger.error("comparison %s failed: %s", comparison_id, message)
            logger.debug("%s", traceback.format_exc())
            try:
                with self._database.connection() as connection:
                    repository.mark_comparison_failed(connection, comparison_id, message)
            except Exception:
                logger.exception("could not record failure for comparison %s", comparison_id)

    # --- loop-side callbacks ------------------------------------------------

    def _publish(self, run_id: UUID, event: Event) -> None:
        self._broker.publish(run_id, event)

    def _close(
        self,
        loop: asyncio.AbstractEventLoop,
        run_id: UUID,
        status: str,
        error: str | None = None,
    ) -> None:
        """Called from the worker thread; hops onto the loop to end the stream."""
        try:
            loop.call_soon_threadsafe(self._broker.close, run_id, status, error)
        except RuntimeError:
            logger.debug("event loop closed; live stream for %s not closed cleanly", run_id)


ROLES = (BASELINE, VARIANT)
