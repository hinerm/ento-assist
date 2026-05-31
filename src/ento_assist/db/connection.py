"""Database connection management for ento-assist.

Provides a context manager for SQLite connections and schema initialization.
All connections enforce foreign keys and WAL mode.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def initialize_db(db_path: str | Path) -> None:
    """Create the database and apply the schema if it does not already exist.

    Safe to call on an existing database — all CREATE statements use
    IF NOT EXISTS, so no data is destroyed on re-initialization.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_sql)


@contextmanager
def get_connection(db_path: str | Path) -> Generator[sqlite3.Connection, None, None]:
    """Yield a configured SQLite connection, committing on clean exit.

    Foreign key enforcement and WAL mode are set on every connection.
    Rolls back automatically if an exception is raised.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
