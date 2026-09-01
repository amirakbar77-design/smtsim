"""Database connection lifecycle.

A psycopg connection pool, opened when the app starts and closed when it stops.
Worker threads borrow connections from the same pool as the event loop does;
psycopg's pool is thread-safe, which is what makes the threading model in
`jobs.py` workable without a second connection strategy.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from smtsim_service.settings import Settings

logger = logging.getLogger(__name__)


class Database:
    """Owns the connection pool. One of these per process."""

    def __init__(self, settings: Settings) -> None:
        self._pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            kwargs={"row_factory": dict_row},
            open=False,
        )

    def open(self, timeout: float = 30.0) -> None:
        self._pool.open(wait=True, timeout=timeout)
        logger.info("database pool opened")

    def close(self) -> None:
        self._pool.close()
        logger.info("database pool closed")

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        with self._pool.connection() as connection:
            yield connection

    def check(self) -> bool:
        """Liveness of the database itself, for /healthz."""
        try:
            with self.connection() as connection:
                connection.execute("SELECT 1")
        except Exception:
            logger.exception("database health check failed")
            return False
        return True
