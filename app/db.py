"""Shared Neon Postgres connection pool, used by ingestion and retrieval."""

import os

from psycopg_pool import ConnectionPool
from pgvector.psycopg import register_vector

_pool: ConnectionPool | None = None


def _configure(conn) -> None:
    register_vector(conn)


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            os.environ["DATABASE_URL"],
            min_size=1,
            max_size=5,
            configure=_configure,
            open=True,
        )
    return _pool
