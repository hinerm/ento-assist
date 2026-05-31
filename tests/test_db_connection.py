# Copyright 2026 The ento-assist Authors
# SPDX-License-Identifier: MIT

"""Tests for db/connection.py — connection manager and schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ento_assist.db.connection import get_connection, initialize_db

_EXPECTED_TABLES = {
    "documents",
    "taxa",
    "identification_keys",
    "couplets",
    "couplet_legs",
    "figures",
    "couplet_leg_figures",
    "glossary_terms",
    "fts_content",
}


def _table_names(db_path: Path) -> set[str]:
    """Return the set of non-system table names in the database."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow') "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# Schema initialization
# ---------------------------------------------------------------------------


def test_schema_created_on_first_open(tmp_path: Path):
    db_path = tmp_path / "fresh.sqlite"
    initialize_db(db_path)
    tables = _table_names(db_path)
    assert _EXPECTED_TABLES.issubset(tables)


def test_schema_idempotent(tmp_path: Path):
    db_path = tmp_path / "idempotent.sqlite"
    initialize_db(db_path)
    # Second call must not raise and must leave tables intact
    initialize_db(db_path)
    assert _EXPECTED_TABLES.issubset(_table_names(db_path))


def test_initialize_db_creates_parent_dirs(tmp_path: Path):
    db_path = tmp_path / "nested" / "dir" / "ento.sqlite"
    initialize_db(db_path)
    assert db_path.exists()


# ---------------------------------------------------------------------------
# PRAGMA settings
# ---------------------------------------------------------------------------


def test_wal_mode_enabled(tmp_path: Path):
    db_path = tmp_path / "wal.sqlite"
    initialize_db(db_path)
    with get_connection(db_path) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_foreign_keys_enforced(tmp_path: Path):
    db_path = tmp_path / "fk.sqlite"
    initialize_db(db_path)
    with get_connection(db_path) as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


# ---------------------------------------------------------------------------
# Row factory
# ---------------------------------------------------------------------------


def test_row_factory_column_by_name(tmp_path: Path):
    db_path = tmp_path / "rowfactory.sqlite"
    initialize_db(db_path)
    import uuid

    doc_id = str(uuid.uuid4())
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, title, path) VALUES (?, ?, ?)",
            (doc_id, "Row Factory Test", "/tmp/test.pdf"),
        )
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT id, title FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row["id"] == doc_id
    assert row["title"] == "Row Factory Test"


# ---------------------------------------------------------------------------
# Context manager: commit on success
# ---------------------------------------------------------------------------


def test_context_manager_commits_on_clean_exit(tmp_path: Path):
    import uuid

    db_path = tmp_path / "commit.sqlite"
    initialize_db(db_path)
    doc_id = str(uuid.uuid4())
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, title, path) VALUES (?, ?, ?)",
            (doc_id, "Commit Test", "/tmp/test.pdf"),
        )
    # Verify data persisted in a fresh connection
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT id FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row is not None


def test_context_manager_rollback_on_exception(tmp_path: Path):
    import uuid

    db_path = tmp_path / "rollback.sqlite"
    initialize_db(db_path)
    doc_id = str(uuid.uuid4())

    with pytest.raises(RuntimeError), get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, title, path) VALUES (?, ?, ?)",
            (doc_id, "Rollback Test", "/tmp/test.pdf"),
        )
        raise RuntimeError("forced rollback")

    # Row must not be present after rollback
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT id FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row is None


def test_no_partial_write_after_rollback(tmp_path: Path):
    import uuid

    db_path = tmp_path / "partial.sqlite"
    initialize_db(db_path)
    doc_id = str(uuid.uuid4())

    with get_connection(db_path) as conn:
        baseline = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    with pytest.raises(ValueError), get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, title, path) VALUES (?, ?, ?)",
            (doc_id, "Partial Test", "/tmp/test.pdf"),
        )
        raise ValueError("abort")

    with get_connection(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == baseline


# ---------------------------------------------------------------------------
# Multiple sequential connections
# ---------------------------------------------------------------------------


def test_multiple_sequential_connections_consistent(tmp_path: Path):
    import uuid

    db_path = tmp_path / "multi.sqlite"
    initialize_db(db_path)
    doc_id = str(uuid.uuid4())

    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, title, path) VALUES (?, ?, ?)",
            (doc_id, "Multi Test", "/tmp/test.pdf"),
        )

    # Second connection should see the committed row
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT title FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row["title"] == "Multi Test"
