# Copyright 2026 The ento-assist Authors
# SPDX-License-Identifier: MIT

"""Tests for utils/merge.py — database merge utility."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from click.testing import CliRunner

from ento_assist.db.connection import get_connection, initialize_db
from ento_assist.utils.merge import main, merge_databases

# ---------------------------------------------------------------------------
# Helpers to build minimal test databases
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path, name: str) -> Path:
    db_path = tmp_path / name
    initialize_db(db_path)
    return db_path


def _insert_doc(db_path: Path, doc_id: str, title: str, path: str = "/tmp/x.pdf") -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, title, path) VALUES (?, ?, ?)",
            (doc_id, title, path),
        )
    # Force WAL checkpoint so shutil.copy2 sees the committed data
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(FULL)")


def _insert_glossary(db_path: Path, term: str, definition: str) -> str:
    term_id = str(uuid.uuid4())
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO glossary_terms (id, term, definition) VALUES (?, ?, ?)",
            (term_id, term, definition),
        )
        conn.execute(
            "INSERT INTO fts_content (content_id, content_type, label, body) VALUES (?, ?, ?, ?)",
            (term_id, "glossary", term, definition),
        )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
    return term_id


def _doc_count(db_path: Path) -> int:
    with get_connection(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


# ---------------------------------------------------------------------------
# Empty merge
# ---------------------------------------------------------------------------


def test_empty_merge_produces_schema(tmp_path: Path):
    db1 = _make_db(tmp_path, "a.sqlite")
    db2 = _make_db(tmp_path, "b.sqlite")
    out = tmp_path / "merged.sqlite"
    warnings = merge_databases(db1, db2, out)
    assert out.exists()
    assert warnings == []
    assert _doc_count(out) == 0


# ---------------------------------------------------------------------------
# Disjoint UUIDs
# ---------------------------------------------------------------------------


def test_disjoint_rows_both_present_after_merge(tmp_path: Path):
    db1 = _make_db(tmp_path, "a.sqlite")
    db2 = _make_db(tmp_path, "b.sqlite")
    id1, id2 = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_doc(db1, id1, "Doc From DB1")
    _insert_doc(db2, id2, "Doc From DB2")

    out = tmp_path / "merged.sqlite"
    merge_databases(db1, db2, out)

    with get_connection(out) as conn:
        ids = {row["id"] for row in conn.execute("SELECT id FROM documents").fetchall()}
    assert id1 in ids
    assert id2 in ids


# ---------------------------------------------------------------------------
# Exact duplicates
# ---------------------------------------------------------------------------


def test_exact_duplicate_skipped_no_error(tmp_path: Path):
    doc_id = str(uuid.uuid4())
    db1 = _make_db(tmp_path, "a.sqlite")
    db2 = _make_db(tmp_path, "b.sqlite")
    _insert_doc(db1, doc_id, "Same Doc", "/tmp/same.pdf")
    _insert_doc(db2, doc_id, "Same Doc", "/tmp/same.pdf")

    out = tmp_path / "merged.sqlite"
    warnings = merge_databases(db1, db2, out)

    assert warnings == []
    assert _doc_count(out) == 1  # not 2


# ---------------------------------------------------------------------------
# Content conflict
# ---------------------------------------------------------------------------


def test_content_conflict_detected_and_logged(tmp_path: Path):
    doc_id = str(uuid.uuid4())
    db1 = _make_db(tmp_path, "a.sqlite")
    db2 = _make_db(tmp_path, "b.sqlite")
    _insert_doc(db1, doc_id, "Title From DB1")
    _insert_doc(db2, doc_id, "Different Title From DB2")

    out = tmp_path / "merged.sqlite"
    warnings = merge_databases(db1, db2, out)

    assert len(warnings) >= 1
    assert any(doc_id in w for w in warnings)


def test_conflict_default_prefer_db1(tmp_path: Path):
    doc_id = str(uuid.uuid4())
    db1 = _make_db(tmp_path, "a.sqlite")
    db2 = _make_db(tmp_path, "b.sqlite")
    _insert_doc(db1, doc_id, "DB1 Title")
    _insert_doc(db2, doc_id, "DB2 Title")

    out = tmp_path / "merged.sqlite"
    merge_databases(db1, db2, out, prefer="db1")

    with get_connection(out) as conn:
        row = conn.execute("SELECT title FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row["title"] == "DB1 Title"


def test_conflict_prefer_db2_wins(tmp_path: Path):
    doc_id = str(uuid.uuid4())
    db1 = _make_db(tmp_path, "a.sqlite")
    db2 = _make_db(tmp_path, "b.sqlite")
    _insert_doc(db1, doc_id, "DB1 Title")
    _insert_doc(db2, doc_id, "DB2 Title")

    out = tmp_path / "merged.sqlite"
    merge_databases(db1, db2, out, prefer="db2")

    with get_connection(out) as conn:
        row = conn.execute("SELECT title FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row["title"] == "DB2 Title"


# ---------------------------------------------------------------------------
# BLOB conflict (figures)
# ---------------------------------------------------------------------------


def test_blob_conflict_does_not_crash(tmp_path: Path):
    doc_id = str(uuid.uuid4())
    fig_id = str(uuid.uuid4())
    db1 = _make_db(tmp_path, "a.sqlite")
    db2 = _make_db(tmp_path, "b.sqlite")

    for db in (db1, db2):
        _insert_doc(db, doc_id, "Doc", "/tmp/x.pdf")

    blob1 = b"\x89PNG\r\n\x1a\nFAKEDATA1"
    blob2 = b"\x89PNG\r\n\x1a\nFAKEDATA2"

    with get_connection(db1) as conn:
        conn.execute(
            "INSERT INTO figures (id, doc_id, page_num, image_data, bbox) VALUES (?, ?, 0, ?, ?)",
            (fig_id, doc_id, blob1, '{"x0":0,"y0":0,"x1":1,"y1":1}'),
        )
    with sqlite3.connect(str(db1)) as conn:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
    with get_connection(db2) as conn:
        conn.execute(
            "INSERT INTO figures (id, doc_id, page_num, image_data, bbox) VALUES (?, ?, 0, ?, ?)",
            (fig_id, doc_id, blob2, '{"x0":0,"y0":0,"x1":1,"y1":1}'),
        )
    with sqlite3.connect(str(db2)) as conn:
        conn.execute("PRAGMA wal_checkpoint(FULL)")

    out = tmp_path / "merged.sqlite"
    # Must not raise
    merge_databases(db1, db2, out)


# ---------------------------------------------------------------------------
# FTS5 sync
# ---------------------------------------------------------------------------


def test_fts_term_queryable_after_merge(tmp_path: Path):
    db1 = _make_db(tmp_path, "a.sqlite")
    db2 = _make_db(tmp_path, "b.sqlite")
    _insert_glossary(db2, "seta", "A bristle-like hair on the cuticle")

    out = tmp_path / "merged.sqlite"
    merge_databases(db1, db2, out)

    with get_connection(out) as conn:
        rows = conn.execute(
            "SELECT label FROM fts_content WHERE fts_content MATCH ?", ("seta",)
        ).fetchall()
    assert any(row["label"] == "seta" for row in rows)


# ---------------------------------------------------------------------------
# FK integrity post-merge
# ---------------------------------------------------------------------------


def test_fk_integrity_after_merge(tmp_path: Path):
    doc_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())

    db1 = _make_db(tmp_path, "a.sqlite")
    db2 = _make_db(tmp_path, "b.sqlite")

    _insert_doc(db1, doc_id, "Doc for FK test")
    with get_connection(db1) as conn:
        conn.execute(
            "INSERT INTO identification_keys (id, doc_id, title) VALUES (?, ?, ?)",
            (key_id, doc_id, "FK Key"),
        )
    with sqlite3.connect(str(db1)) as conn:
        conn.execute("PRAGMA wal_checkpoint(FULL)")

    out = tmp_path / "merged.sqlite"
    merge_databases(db1, db2, out)

    # Re-enable FK checks and verify no violations
    with sqlite3.connect(str(out)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert violations == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_exit_code_success(tmp_path: Path):
    db1 = _make_db(tmp_path, "a.sqlite")
    db2 = _make_db(tmp_path, "b.sqlite")
    out = tmp_path / "out.sqlite"

    runner = CliRunner()
    result = runner.invoke(main, [str(db1), str(db2), "--output", str(out)])
    assert result.exit_code == 0


def test_cli_missing_output_arg_fails(tmp_path: Path):
    db1 = _make_db(tmp_path, "a.sqlite")
    db2 = _make_db(tmp_path, "b.sqlite")

    runner = CliRunner()
    result = runner.invoke(main, [str(db1), str(db2)])  # no --output
    assert result.exit_code != 0


def test_cli_nonexistent_db_fails(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            str(tmp_path / "noexist1.sqlite"),
            str(tmp_path / "noexist2.sqlite"),
            "--output",
            str(tmp_path / "out.sqlite"),
        ],
    )
    assert result.exit_code != 0
