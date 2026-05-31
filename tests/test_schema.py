# Copyright 2026 The ento-assist Authors
# SPDX-License-Identifier: MIT

"""Tests for db/schema.sql — structural validation via PRAGMA queries."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from ento_assist.db.connection import get_connection, initialize_db

_EXPECTED_TABLES = [
    "documents",
    "taxa",
    "identification_keys",
    "couplets",
    "couplet_legs",
    "figures",
    "couplet_leg_figures",
    "glossary_terms",
    "fts_content",
]


@pytest.fixture()
def schema_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "schema_test.sqlite"
    initialize_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# Table existence
# ---------------------------------------------------------------------------


def test_all_tables_exist(schema_db: Path):
    with get_connection(schema_db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow') "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    names = {row["name"] for row in rows}
    for table in _EXPECTED_TABLES:
        assert table in names, f"Expected table '{table}' not found in schema"


# ---------------------------------------------------------------------------
# Column existence spot-checks
# ---------------------------------------------------------------------------


def test_couplets_has_page_ref_column(schema_db: Path):
    with get_connection(schema_db) as conn:
        info = conn.execute("PRAGMA table_info(couplets)").fetchall()
    col_names = {row["name"] for row in info}
    assert "page_ref" in col_names


def test_documents_has_path_column(schema_db: Path):
    with get_connection(schema_db) as conn:
        info = conn.execute("PRAGMA table_info(documents)").fetchall()
    col_names = {row["name"] for row in info}
    assert "path" in col_names


def test_figures_has_image_data_blob_column(schema_db: Path):
    with get_connection(schema_db) as conn:
        info = conn.execute("PRAGMA table_info(figures)").fetchall()
    col_names = {row["name"] for row in info}
    assert "image_data" in col_names
    assert "bbox" in col_names


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


def test_leg_label_check_accepts_a_and_b(schema_db: Path):
    doc_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())
    c_id = str(uuid.uuid4())
    with get_connection(schema_db) as conn:
        conn.execute(
            "INSERT INTO documents (id, title, path) VALUES (?, ?, ?)",
            (doc_id, "doc", "/tmp/d.pdf"),
        )
        conn.execute(
            "INSERT INTO identification_keys (id, doc_id, title) VALUES (?, ?, ?)",
            (key_id, doc_id, "k"),
        )
        conn.execute(
            "INSERT INTO couplets (id, key_id, number) VALUES (?, ?, ?)",
            (c_id, key_id, "1"),
        )
        # Both 'A' and 'B' must be accepted
        conn.execute(
            "INSERT INTO couplet_legs (id, couplet_id, leg_label, text) VALUES (?, ?, 'A', 'a')",
            (str(uuid.uuid4()), c_id),
        )
        conn.execute(
            "INSERT INTO couplet_legs (id, couplet_id, leg_label, text) VALUES (?, ?, 'B', 'b')",
            (str(uuid.uuid4()), c_id),
        )


def test_leg_label_check_rejects_invalid_value(schema_db: Path):
    doc_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())
    c_id = str(uuid.uuid4())
    with get_connection(schema_db) as conn:
        conn.execute(
            "INSERT INTO documents (id, title, path) VALUES (?, ?, ?)",
            (doc_id, "doc", "/tmp/d.pdf"),
        )
        conn.execute(
            "INSERT INTO identification_keys (id, doc_id, title) VALUES (?, ?, ?)",
            (key_id, doc_id, "k"),
        )
        conn.execute(
            "INSERT INTO couplets (id, key_id, number) VALUES (?, ?, ?)",
            (c_id, key_id, "1"),
        )

    with pytest.raises(sqlite3.IntegrityError), get_connection(schema_db) as conn:
        conn.execute(
            "INSERT INTO couplet_legs (id, couplet_id, leg_label, text) VALUES (?, ?, 'C', 'bad')",
            (str(uuid.uuid4()), c_id),
        )


def test_glossary_term_unique_constraint(schema_db: Path):
    term_id1 = str(uuid.uuid4())
    term_id2 = str(uuid.uuid4())
    with get_connection(schema_db) as conn:
        conn.execute(
            "INSERT INTO glossary_terms (id, term, definition) VALUES (?, ?, ?)",
            (term_id1, "tarsus", "The distal leg segment"),
        )
    with pytest.raises(sqlite3.IntegrityError), get_connection(schema_db) as conn:
        conn.execute(
            "INSERT INTO glossary_terms (id, term, definition) VALUES (?, ?, ?)",
            (term_id2, "tarsus", "Duplicate definition"),
        )


# ---------------------------------------------------------------------------
# FTS5 virtual table
# ---------------------------------------------------------------------------


def test_fts5_content_is_queryable(schema_db: Path):
    """FTS5 table must accept a MATCH query without error."""
    with get_connection(schema_db) as conn:
        # Should not raise even with no matching rows
        rows = conn.execute(
            "SELECT * FROM fts_content WHERE fts_content MATCH ? LIMIT 5",
            ("mosquito",),
        ).fetchall()
    assert isinstance(rows, list)


def test_fts5_content_insert_and_search(schema_db: Path):
    content_id = str(uuid.uuid4())
    with get_connection(schema_db) as conn:
        conn.execute(
            "INSERT INTO fts_content (content_id, content_type, label, body) VALUES (?, ?, ?, ?)",
            (content_id, "glossary", "tarsus", "The distal leg segment bearing the claws"),
        )
    with get_connection(schema_db) as conn:
        rows = conn.execute(
            "SELECT content_id, label FROM fts_content WHERE fts_content MATCH ?",
            ("tarsus",),
        ).fetchall()
    assert any(row["content_id"] == content_id for row in rows)


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


def test_index_on_taxa_name_exists(schema_db: Path):
    with get_connection(schema_db) as conn:
        indexes = conn.execute("PRAGMA index_list(taxa)").fetchall()
    index_names = {row["name"] for row in indexes}
    assert any("taxa_name" in name or "name" in name for name in index_names)


def test_index_on_couplet_legs_couplet_exists(schema_db: Path):
    with get_connection(schema_db) as conn:
        indexes = conn.execute("PRAGMA index_list(couplet_legs)").fetchall()
    index_names = {row["name"] for row in indexes}
    assert any("couplet" in name for name in index_names)
