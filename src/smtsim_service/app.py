"""The HTTP and WebSocket API.

This module is the second consumer of the simulation. The first is the CLI, and
the two share every line of the model, the metrics and the event schema; what
differs is only where the events go and who is asking.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse

from smtsim_service import __version__, repository
from smtsim_service.db import Database
from smtsim_service.jobs import JobRunner
from smtsim_service.schemas import (
    Accepted,
    ComparisonDetail,
    ComparisonRequest,
    ComparisonRunLink,
    EventPage,
    EventRecord,
    Health,
    RunDetail,
    RunPage,
    RunRequest,
    RunSummary,
    build_config,
)
from smtsim_service.settings import Settings, get_settings
from smtsim_service.streaming import EventBroker, next_batch

logger = logging.getLogger(__name__)

MAX_PAGE = 1000
WS_TRY_AGAIN_LATER = 1013

DESCRIPTION = """
Discrete-event simulation of an SMT (surface-mount technology) assembly line.

Submit a line configuration, get a job id, poll or subscribe for the result. The
simulation runs off the event loop in a worker thread; the summary this API
returns is the same structure the `smtsim` CLI prints, including the four time
accounts and the reliability figures.

There is no authentication. See the README.
"""


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_runner(request: Request) -> JobRunner:
    return request.app.state.runner


def get_service_settings(request: Request) -> Settings:
    return request.app.state.settings


# Module level, not closure level: FastAPI resolves these annotations against
# the module namespace, so an alias defined inside create_app is invisible to it
# and every dependency silently becomes a required query parameter.
DatabaseDep = Annotated[Database, Depends(get_database)]
RunnerDep = Annotated[JobRunner, Depends(get_runner)]
SettingsDep = Annotated[Settings, Depends(get_service_settings)]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(settings)
        database.open()
        broker = EventBroker(queue_maxsize=settings.ws_queue_maxsize)
        runner = JobRunner(database, broker, settings)

        app.state.settings = settings
        app.state.database = database
        app.state.broker = broker
        app.state.runner = runner
        try:
            yield
        finally:
            runner.shutdown(wait=True)
            database.close()

    app = FastAPI(
        title="smtsim",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )

    # --- health -------------------------------------------------------------

    @app.get("/healthz", response_model=Health, tags=["meta"])
    def healthz(database: DatabaseDep, runner: RunnerDep) -> Response:
        reachable = database.check()
        body = Health(
            status="ok" if reachable else "degraded",
            database=reachable,
            version=__version__,
            in_flight=runner.in_flight,
        )
        return JSONResponse(body.model_dump(), status_code=200 if reachable else 503)

    # --- runs ---------------------------------------------------------------

    @app.post("/runs", response_model=Accepted, status_code=202, tags=["runs"])
    def create_run(
        request: RunRequest,
        database: DatabaseDep,
        runner: RunnerDep,
        config_settings: SettingsDep,
    ) -> Accepted:
        if request.minutes > config_settings.max_minutes:
            raise HTTPException(422, f"minutes must not exceed {config_settings.max_minutes:g}")
        if request.warmup_minutes >= request.minutes:
            raise HTTPException(422, "warmup_minutes must be shorter than minutes")

        config = request.line_config()
        run_id = uuid4()
        with database.connection() as connection:
            repository.create_run(
                connection,
                run_id,
                seed=request.seed,
                minutes=request.minutes,
                warmup_minutes=request.warmup_minutes,
                config=config.to_dict(),
                stores_events=request.store_events,
            )
        runner.submit_run(
            run_id, config, request.minutes, request.warmup_minutes, request.store_events
        )
        return Accepted(id=run_id, status="queued")

    @app.get("/runs", response_model=RunPage, tags=["runs"])
    def list_runs(
        database: DatabaseDep,
        status: str | None = Query(default=None, pattern="^(queued|running|succeeded|failed)$"),
        limit: int = Query(default=50, ge=1, le=MAX_PAGE),
        offset: int = Query(default=0, ge=0),
    ) -> RunPage:
        with database.connection() as connection:
            rows, total = repository.list_runs(connection, status, limit, offset)
        return RunPage(
            items=[RunSummary(**row) for row in rows], total=total, limit=limit, offset=offset
        )

    @app.get("/runs/{run_id}", response_model=RunDetail, tags=["runs"])
    def get_run(run_id: UUID, database: DatabaseDep) -> RunDetail:
        with database.connection() as connection:
            row = repository.get_run(connection, run_id)
        if row is None:
            raise HTTPException(404, f"no run {run_id}")
        return RunDetail(**row)

    @app.delete("/runs/{run_id}", status_code=204, tags=["runs"])
    def delete_run(run_id: UUID, database: DatabaseDep) -> Response:
        with database.connection() as connection:
            deleted = repository.delete_run(connection, run_id)
        if not deleted:
            raise HTTPException(404, f"no run {run_id}")
        return Response(status_code=204)

    @app.get("/runs/{run_id}/events", response_model=EventPage, tags=["runs"])
    def get_run_events(
        run_id: UUID,
        database: DatabaseDep,
        after: int = Query(
            default=0, ge=0, description="Return events with seq greater than this."
        ),
        limit: int = Query(default=500, ge=1, le=MAX_PAGE),
    ) -> EventPage:
        with database.connection() as connection:
            if repository.get_run(connection, run_id) is None:
                raise HTTPException(404, f"no run {run_id}")
            rows = repository.read_events(connection, run_id, after, limit)
        return EventPage(
            run_id=run_id,
            items=[EventRecord(**row) for row in rows],
            next_after=rows[-1]["seq"] if len(rows) == limit else None,
            limit=limit,
        )

    # --- live stream --------------------------------------------------------

    @app.websocket("/runs/{run_id}/stream")
    async def stream_run(websocket: WebSocket, run_id: UUID) -> None:
        """Live events, or a replay of the stored ones if the run has finished.

        Stage 4 gets exactly one code path: connect, read batches until the
        stream closes. Whether the events are arriving from a worker thread or
        being read back out of Postgres is not the client's problem.
        """
        database: Database = app.state.database
        broker: EventBroker = app.state.broker

        with database.connection() as connection:
            run = repository.get_run(connection, run_id)
        if run is None:
            await websocket.close(code=4404, reason=f"no run {run_id}")
            return

        await websocket.accept()

        if run["status"] in {"succeeded", "failed"}:
            await _replay(websocket, database, run_id, run, settings)
            return

        try:
            with broker.subscribe(run_id) as subscriber:
                await websocket.send_json({"type": "start", "mode": "live", "run_id": str(run_id)})
                while True:
                    batch, closing = await next_batch(
                        subscriber, settings.ws_batch_size, settings.ws_batch_interval_ms
                    )
                    if subscriber.dropped:
                        await websocket.close(
                            code=WS_TRY_AGAIN_LATER,
                            reason="client fell behind; use GET /runs/{id}/events",
                        )
                        return
                    if batch:
                        await websocket.send_json({"type": "events", "events": batch})
                    if closing is not None:
                        await websocket.send_json({"type": "end", **closing})
                        await websocket.close()
                        return
        except WebSocketDisconnect:
            logger.debug("client disconnected from run %s", run_id)

    async def _replay(
        websocket: WebSocket,
        database: Database,
        run_id: UUID,
        run: dict[str, Any],
        settings: Settings,
    ) -> None:
        try:
            await websocket.send_json({"type": "start", "mode": "replay", "run_id": str(run_id)})
            with database.connection() as connection:
                for rows in repository.iter_events(connection, run_id, settings.ws_batch_size):
                    await websocket.send_json(
                        {
                            "type": "events",
                            "events": [
                                {k: v for k, v in row.items() if k != "seq"} for row in rows
                            ],
                        }
                    )
            await websocket.send_json(
                {"type": "end", "status": run["status"], "error": run["error"]}
            )
            await websocket.close()
        except WebSocketDisconnect:
            logger.debug("client disconnected during replay of run %s", run_id)

    # --- comparisons --------------------------------------------------------

    @app.post("/comparisons", response_model=Accepted, status_code=202, tags=["comparisons"])
    def create_comparison(
        request: ComparisonRequest,
        database: DatabaseDep,
        runner: RunnerDep,
        config_settings: SettingsDep,
    ) -> Accepted:
        if request.seeds > config_settings.max_comparison_seeds:
            raise HTTPException(
                422, f"seeds must not exceed {config_settings.max_comparison_seeds}"
            )
        if request.minutes > config_settings.max_minutes:
            raise HTTPException(422, f"minutes must not exceed {config_settings.max_minutes:g}")
        if request.warmup_minutes >= request.minutes:
            raise HTTPException(422, "warmup_minutes must be shorter than minutes")

        baseline = build_config(request.baseline)
        variant = build_config(request.variant)
        comparison_id = uuid4()
        with database.connection() as connection:
            repository.create_comparison(
                connection,
                comparison_id,
                baseline_config=baseline.to_dict(),
                variant_config=variant.to_dict(),
                seeds=request.seeds,
                minutes=request.minutes,
                warmup_minutes=request.warmup_minutes,
            )
        runner.submit_comparison(
            comparison_id,
            baseline,
            variant,
            request.seeds,
            request.minutes,
            request.warmup_minutes,
            request.store_events,
        )
        return Accepted(id=comparison_id, status="queued")

    @app.get("/comparisons/{comparison_id}", response_model=ComparisonDetail, tags=["comparisons"])
    def get_comparison(comparison_id: UUID, database: DatabaseDep) -> ComparisonDetail:
        with database.connection() as connection:
            row = repository.get_comparison(connection, comparison_id)
            if row is None:
                raise HTTPException(404, f"no comparison {comparison_id}")
            links = repository.list_comparison_runs(connection, comparison_id)
        return ComparisonDetail(
            **row,
            runs=[
                ComparisonRunLink(
                    run_id=link["run_id"],
                    role=link["role"],
                    seed=link["seed"],
                    status=link["status"],
                )
                for link in links
            ],
        )

    return app


app = create_app  # uvicorn factory target: `uvicorn smtsim_service.app:app --factory`
