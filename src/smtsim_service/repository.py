"""All the SQL.

Kept in one module so that the shape of the schema is visible in one place, and
so that nothing above this layer has to know what a cursor is.

Events are written with COPY rather than executemany. A single shift is about
7,000 events and a 60-seed comparison is about 400,000; at that volume the
per-statement round trip dominates everything else, and COPY turns the write
into one streamed transfer.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Json, Jsonb

from smtsim.events import Event, EventType

RUN_COLUMNS = """
    id, status, seed, minutes, warmup_minutes, config, summary,
    event_count, stores_events, error, created_at, started_at, finished_at
"""

COMPARISON_COLUMNS = """
    id, status, baseline_config, variant_config, seeds, minutes,
    warmup_minutes, summary, error, created_at, started_at, finished_at
"""


# --- runs -------------------------------------------------------------------


def create_run(
    connection: Connection,
    run_id: UUID,
    seed: int,
    minutes: float,
    warmup_minutes: float,
    config: dict[str, Any],
    stores_events: bool = True,
    status: str = "queued",
) -> dict[str, Any]:
    row = connection.execute(
        f"""
        INSERT INTO runs (id, status, seed, minutes, warmup_minutes, config, stores_events)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING {RUN_COLUMNS}
        """,
        (run_id, status, seed, minutes, warmup_minutes, Jsonb(config), stores_events),
    ).fetchone()
    assert row is not None
    return row


def mark_run_started(connection: Connection, run_id: UUID) -> None:
    connection.execute(
        "UPDATE runs SET status = 'running', started_at = now() WHERE id = %s", (run_id,)
    )


def mark_run_succeeded(
    connection: Connection, run_id: UUID, summary: dict[str, Any], event_count: int
) -> None:
    connection.execute(
        """
        UPDATE runs
           SET status = 'succeeded', summary = %s, event_count = %s, finished_at = now()
         WHERE id = %s
        """,
        (Jsonb(summary), event_count, run_id),
    )


def mark_run_failed(connection: Connection, run_id: UUID, error: str) -> None:
    connection.execute(
        """
        UPDATE runs
           SET status = 'failed', error = %s, finished_at = now()
         WHERE id = %s
        """,
        (error[:8000], run_id),
    )


def get_run(connection: Connection, run_id: UUID) -> dict[str, Any] | None:
    return connection.execute(
        f"SELECT {RUN_COLUMNS} FROM runs WHERE id = %s", (run_id,)
    ).fetchone()


def list_runs(
    connection: Connection, status: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    if status is None:
        total = connection.execute("SELECT count(*) AS n FROM runs").fetchone()["n"]
        rows = connection.execute(
            f"SELECT {RUN_COLUMNS} FROM runs ORDER BY created_at DESC, id LIMIT %s OFFSET %s",
            (limit, offset),
        ).fetchall()
    else:
        total = connection.execute(
            "SELECT count(*) AS n FROM runs WHERE status = %s", (status,)
        ).fetchone()["n"]
        rows = connection.execute(
            f"""
            SELECT {RUN_COLUMNS} FROM runs WHERE status = %s
            ORDER BY created_at DESC, id LIMIT %s OFFSET %s
            """,
            (status, limit, offset),
        ).fetchall()
    return rows, total


def delete_run(connection: Connection, run_id: UUID) -> bool:
    """Delete a run. Events and comparison links cascade in the schema."""
    result = connection.execute("DELETE FROM runs WHERE id = %s", (run_id,))
    return result.rowcount > 0


# --- events -----------------------------------------------------------------


def copy_events(
    connection: Connection,
    run_id: UUID,
    events: Sequence[Event],
    first_seq: int,
) -> int:
    """Stream a batch of events into run_events with COPY.

    ``detail`` is serialised here with the same separators the JSONL sink uses,
    into a `json` column that stores the text verbatim, so a stored log can be
    reproduced byte for byte.
    """
    if not events:
        return 0

    with connection.cursor().copy(
        "COPY run_events (run_id, seq, t, event, board, station, detail) FROM STDIN"
    ) as copy:
        for offset, event in enumerate(events):
            copy.write_row(
                (
                    run_id,
                    first_seq + offset,
                    event.time,
                    str(event.type),
                    event.board_id,
                    event.station,
                    None
                    if event.detail is None
                    else Json(dict(event.detail), dumps=_compact_json),
                )
            )
    return len(events)


def _compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=False)


def count_events(connection: Connection, run_id: UUID) -> int:
    row = connection.execute(
        "SELECT count(*) AS n FROM run_events WHERE run_id = %s", (run_id,)
    ).fetchone()
    return int(row["n"])


def read_events(
    connection: Connection, run_id: UUID, after: int, limit: int
) -> list[dict[str, Any]]:
    return connection.execute(
        """
        SELECT seq, t, event, board, station, detail
          FROM run_events
         WHERE run_id = %s AND seq > %s
         ORDER BY seq
         LIMIT %s
        """,
        (run_id, after, limit),
    ).fetchall()


def iter_events(
    connection: Connection, run_id: UUID, batch: int = 1000
) -> Iterator[list[dict[str, Any]]]:
    """Walk a run's whole event stream in batches, for WebSocket replay."""
    after = 0
    while True:
        rows = read_events(connection, run_id, after, batch)
        if not rows:
            return
        yield rows
        after = rows[-1]["seq"]


def row_to_event(row: dict[str, Any]) -> Event:
    return Event(
        time=float(row["t"]),
        type=EventType(row["event"]),
        board_id=row["board"],
        station=row["station"],
        detail=row["detail"],
    )


def rows_to_events(rows: Iterable[dict[str, Any]]) -> list[Event]:
    return [row_to_event(row) for row in rows]


# --- comparisons ------------------------------------------------------------


def create_comparison(
    connection: Connection,
    comparison_id: UUID,
    baseline_config: dict[str, Any],
    variant_config: dict[str, Any],
    seeds: int,
    minutes: float,
    warmup_minutes: float,
) -> dict[str, Any]:
    row = connection.execute(
        f"""
        INSERT INTO comparisons
            (id, status, baseline_config, variant_config, seeds, minutes, warmup_minutes)
        VALUES (%s, 'queued', %s, %s, %s, %s, %s)
        RETURNING {COMPARISON_COLUMNS}
        """,
        (
            comparison_id,
            Jsonb(baseline_config),
            Jsonb(variant_config),
            seeds,
            minutes,
            warmup_minutes,
        ),
    ).fetchone()
    assert row is not None
    return row


def mark_comparison_started(connection: Connection, comparison_id: UUID) -> None:
    connection.execute(
        "UPDATE comparisons SET status = 'running', started_at = now() WHERE id = %s",
        (comparison_id,),
    )


def mark_comparison_succeeded(
    connection: Connection, comparison_id: UUID, summary: dict[str, Any]
) -> None:
    connection.execute(
        """
        UPDATE comparisons
           SET status = 'succeeded', summary = %s, finished_at = now()
         WHERE id = %s
        """,
        (Jsonb(summary), comparison_id),
    )


def mark_comparison_failed(connection: Connection, comparison_id: UUID, error: str) -> None:
    connection.execute(
        """
        UPDATE comparisons
           SET status = 'failed', error = %s, finished_at = now()
         WHERE id = %s
        """,
        (error[:8000], comparison_id),
    )


def get_comparison(connection: Connection, comparison_id: UUID) -> dict[str, Any] | None:
    return connection.execute(
        f"SELECT {COMPARISON_COLUMNS} FROM comparisons WHERE id = %s", (comparison_id,)
    ).fetchone()


def link_comparison_run(
    connection: Connection, comparison_id: UUID, run_id: UUID, role: str, seed: int
) -> None:
    connection.execute(
        """
        INSERT INTO comparison_runs (comparison_id, run_id, role, seed)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (comparison_id, run_id, role, seed),
    )


def list_comparison_runs(connection: Connection, comparison_id: UUID) -> list[dict[str, Any]]:
    return connection.execute(
        """
        SELECT cr.run_id, cr.role, cr.seed, r.status, r.summary
          FROM comparison_runs cr
          JOIN runs r ON r.id = cr.run_id
         WHERE cr.comparison_id = %s
         ORDER BY cr.seed, cr.role
        """,
        (comparison_id,),
    ).fetchall()
