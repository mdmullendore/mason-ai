"""Idempotent schema setup — safe to run against a fresh Neon database or a
database that already has the schema. Also called at the start of ingest.py.

Uses a plain, one-off connection rather than app.db's pooled connection: the pool's
configure hook registers the pgvector type adapter on every connection, which requires
the `vector` extension to already exist — a chicken-and-egg problem on a brand-new
database, since that's exactly what this script creates.

    python scripts/init_db.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import psycopg  # noqa: E402

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks (
    id SERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding VECTOR(768) NOT NULL
);
"""


def init_db() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(SCHEMA_SQL)


if __name__ == "__main__":
    init_db()
    print("Schema ready (vector extension + chunks table).")
