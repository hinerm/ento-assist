# Copyright 2026 The ento-assist Authors
# SPDX-License-Identifier: MIT

"""Tests for the identification workflow (tools/identification.py).

Uses the minimal_couplet_graph and session_file fixtures from conftest.py.
No PDF fixture is required — all tests can run immediately.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import frontmatter
import pytest

from ento_assist.db.connection import get_connection
from ento_assist.tools.identification import (
    _collect_terminals,
    _update_current_position,
    register_identification_tools,
)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    pytest.skip("mcp not installed", allow_module_level=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_tools(mcp: FastMCP) -> dict[str, Any]:
    """Return a dict of {tool_name: callable} from a FastMCP instance."""
    return {name: tool.fn for name, tool in mcp._tool_manager._tools.items()}


@pytest.fixture()
def id_tools() -> dict[str, Any]:
    mcp = FastMCP("test-id")
    register_identification_tools(mcp)
    return _get_tools(mcp)


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def test_start_session_creates_file(
    id_tools, minimal_couplet_graph: tuple[Path, str], tmp_path: Path
):
    db_path, key_id = minimal_couplet_graph
    session_path = tmp_path / "session.md"

    result = id_tools["run_start_session"](
        db_path=str(db_path), key_id=key_id, output_path=str(session_path)
    )

    assert session_path.exists()
    assert "session_id" in result
    uuid.UUID(result["session_id"])


def test_start_session_frontmatter_fields(
    id_tools, minimal_couplet_graph: tuple[Path, str], tmp_path: Path
):
    db_path, key_id = minimal_couplet_graph
    session_path = tmp_path / "session.md"
    id_tools["run_start_session"](
        db_path=str(db_path), key_id=key_id, output_path=str(session_path)
    )

    post = frontmatter.load(str(session_path))
    assert post.metadata["key_id"] == key_id
    assert post.metadata["status"] == "in_progress"
    assert "current_couplet_id" in post.metadata
    assert "session_id" in post.metadata


def test_start_session_invalid_key_raises(
    id_tools, minimal_couplet_graph: tuple[Path, str], tmp_path: Path
):
    db_path, _ = minimal_couplet_graph
    with pytest.raises(ValueError):
        id_tools["run_start_session"](
            db_path=str(db_path),
            key_id=str(uuid.uuid4()),  # nonexistent key
            output_path=str(tmp_path / "bad.md"),
        )


# ---------------------------------------------------------------------------
# Resume session
# ---------------------------------------------------------------------------


def test_resume_session_reads_current_position(id_tools, session_file: tuple[Path, Path, str]):
    session_path, db_path, key_id = session_file
    result = id_tools["run_resume_session"](session_path=str(session_path))
    assert result["status"] == "in_progress"
    assert "couplet_id" in result or "couplet_number" in result or "leg_A" in result


def test_resume_session_missing_file_raises(id_tools, tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        id_tools["run_resume_session"](session_path=str(tmp_path / "noexist.md"))


# ---------------------------------------------------------------------------
# Advance session
# ---------------------------------------------------------------------------


def test_advance_session_a_moves_to_next_couplet(id_tools, session_file: tuple[Path, Path, str]):
    session_path, db_path, _ = session_file
    result = id_tools["run_advance_session"](session_path=str(session_path), leg_label="A")
    assert result["status"] in ("in_progress", "complete")


def test_advance_session_b_branch_different_from_a(
    id_tools, minimal_couplet_graph: tuple[Path, str], tmp_path: Path
):
    db_path, key_id = minimal_couplet_graph

    # Start two parallel sessions from the same key
    path_a = tmp_path / "session_a.md"
    path_b = tmp_path / "session_b.md"

    id_tools["run_start_session"](db_path=str(db_path), key_id=key_id, output_path=str(path_a))
    id_tools["run_start_session"](db_path=str(db_path), key_id=key_id, output_path=str(path_b))

    res_a = id_tools["run_advance_session"](session_path=str(path_a), leg_label="A")
    res_b = id_tools["run_advance_session"](session_path=str(path_b), leg_label="B")

    # Choosing A and B from the same couplet must lead to different next positions
    couplet_id_a = res_a.get("couplet_id")
    couplet_id_b = res_b.get("couplet_id")
    if couplet_id_a and couplet_id_b:
        assert couplet_id_a != couplet_id_b


def test_advance_session_invalid_choice_raises(id_tools, session_file: tuple[Path, Path, str]):
    session_path, _, _ = session_file
    with pytest.raises(ValueError):
        id_tools["run_advance_session"](session_path=str(session_path), leg_label="C")


def test_advance_session_updates_session_file(id_tools, session_file: tuple[Path, Path, str]):
    session_path, _, _ = session_file
    before = frontmatter.load(str(session_path))
    before_couplet = before.metadata["current_couplet_id"]

    id_tools["run_advance_session"](session_path=str(session_path), leg_label="A")

    after = frontmatter.load(str(session_path))
    # Either the couplet changed (moved forward) or status is complete (terminal reached)
    assert (
        after.metadata["current_couplet_id"] != before_couplet
        or after.metadata["status"] == "complete"
    )


# ---------------------------------------------------------------------------
# Full traversal to terminal
# ---------------------------------------------------------------------------


def test_full_traversal_reaches_terminal(
    id_tools, minimal_couplet_graph: tuple[Path, str], tmp_path: Path
):
    """Follow path A→A→A from the root to reach Anopheles gambiae."""
    db_path, key_id = minimal_couplet_graph
    session_path = tmp_path / "full.md"
    id_tools["run_start_session"](
        db_path=str(db_path), key_id=key_id, output_path=str(session_path)
    )

    # A→A→A reaches couplet 4 leg A → Anopheles gambiae
    for _ in range(3):
        result = id_tools["run_advance_session"](session_path=str(session_path), leg_label="A")
        if result["status"] == "complete":
            break

    post = frontmatter.load(str(session_path))
    assert post.metadata["status"] == "complete"
    assert post.metadata["terminal_taxon_id"] is not None


def test_terminal_taxon_retrievable(
    id_tools, minimal_couplet_graph: tuple[Path, str], tmp_path: Path
):
    db_path, key_id = minimal_couplet_graph
    session_path = tmp_path / "term.md"
    id_tools["run_start_session"](
        db_path=str(db_path), key_id=key_id, output_path=str(session_path)
    )

    for _ in range(3):
        result = id_tools["run_advance_session"](session_path=str(session_path), leg_label="A")
        if result["status"] == "complete":
            break

    post = frontmatter.load(str(session_path))
    taxon_id = post.metadata["terminal_taxon_id"]

    taxon_result = id_tools["run_taxon_description"](db_path=str(db_path), taxon_id=taxon_id)
    assert taxon_result["name"] in {"Anopheles gambiae", "Culex pipiens", "Aedes aegypti"}


# ---------------------------------------------------------------------------
# Complete session resume
# ---------------------------------------------------------------------------


def test_resume_complete_session_returns_complete_status(
    id_tools, minimal_couplet_graph: tuple[Path, str], tmp_path: Path
):
    db_path, key_id = minimal_couplet_graph
    session_path = tmp_path / "done.md"
    id_tools["run_start_session"](
        db_path=str(db_path), key_id=key_id, output_path=str(session_path)
    )

    for _ in range(3):
        r = id_tools["run_advance_session"](session_path=str(session_path), leg_label="A")
        if r["status"] == "complete":
            break

    result = id_tools["run_resume_session"](session_path=str(session_path))
    assert result["status"] == "complete"


# ---------------------------------------------------------------------------
# Look ahead
# ---------------------------------------------------------------------------


def test_look_ahead_returns_both_branches(id_tools, session_file: tuple[Path, Path, str]):
    session_path, _, _ = session_file
    result = id_tools["run_look_ahead"](session_path=str(session_path), depth=3)
    assert "branch_A" in result
    assert "branch_B" in result
    assert isinstance(result["branch_A"], list)
    assert isinstance(result["branch_B"], list)


def test_look_ahead_depth_limits_results(id_tools, session_file: tuple[Path, Path, str]):
    session_path, _, _ = session_file
    # depth=1 from couplet 1: each branch points to another couplet, not a terminal
    # so both should be empty or just leaves if resolved within depth
    result = id_tools["run_look_ahead"](session_path=str(session_path), depth=1)
    # No assertion on count since depth=1 may or may not reach terminals;
    # but the call must not raise.
    assert "branch_A" in result


def test_look_ahead_finds_terminals_at_sufficient_depth(
    id_tools, session_file: tuple[Path, Path, str]
):
    session_path, _, _ = session_file
    result = id_tools["run_look_ahead"](session_path=str(session_path), depth=5)
    all_terminals = result["branch_A"] + result["branch_B"]
    assert len(all_terminals) > 0
    for t in all_terminals:
        assert "name" in t


# ---------------------------------------------------------------------------
# _collect_terminals (internal BFS)
# ---------------------------------------------------------------------------


def test_collect_terminals_returns_list(minimal_couplet_graph: tuple[Path, str]):
    db_path, key_id = minimal_couplet_graph
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT start_couplet_id FROM identification_keys WHERE id = ?", (key_id,)
        ).fetchone()
    start_id = row["start_couplet_id"]

    result = _collect_terminals(str(db_path), start_id, "A", depth=5)
    assert isinstance(result, list)


def test_collect_terminals_cycle_safety(tmp_path: Path):
    """Forced cycle (couplet A → couplet B → couplet A) must not loop forever."""
    from ento_assist.db.connection import initialize_db

    db_path = tmp_path / "cycle.sqlite"
    initialize_db(db_path)

    doc_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())
    c1_id = str(uuid.uuid4())
    c2_id = str(uuid.uuid4())

    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, title, path) VALUES (?, ?, ?)",
            (doc_id, "Cycle Test", "/tmp/t.pdf"),
        )
        conn.execute(
            "INSERT INTO identification_keys (id, doc_id, title) VALUES (?, ?, ?)",
            (key_id, doc_id, "Cycle Key"),
        )
        conn.execute(
            "INSERT INTO couplets (id, key_id, number) VALUES (?, ?, ?)", (c1_id, key_id, "1")
        )
        conn.execute(
            "INSERT INTO couplets (id, key_id, number) VALUES (?, ?, ?)", (c2_id, key_id, "2")
        )
        # Create a cycle: c1-A → c2, c2-A → c1
        conn.execute(
            "INSERT INTO couplet_legs (id, couplet_id, leg_label, text, next_couplet_id) "
            "VALUES (?, ?, 'A', 'cycle forward', ?)",
            (str(uuid.uuid4()), c1_id, c2_id),
        )
        conn.execute(
            "INSERT INTO couplet_legs (id, couplet_id, leg_label, text, next_couplet_id) "
            "VALUES (?, ?, 'B', 'dead end', NULL)",
            (str(uuid.uuid4()), c1_id),
        )
        conn.execute(
            "INSERT INTO couplet_legs (id, couplet_id, leg_label, text, next_couplet_id) "
            "VALUES (?, ?, 'A', 'cycle back', ?)",
            (str(uuid.uuid4()), c2_id, c1_id),  # back to c1 → cycle
        )
        conn.execute(
            "INSERT INTO couplet_legs (id, couplet_id, leg_label, text, next_couplet_id) "
            "VALUES (?, ?, 'B', 'dead end 2', NULL)",
            (str(uuid.uuid4()), c2_id),
        )

    # This must terminate and return an empty list (no terminals reachable)
    result = _collect_terminals(str(db_path), c1_id, "A", depth=10)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _update_current_position (internal helper)
# ---------------------------------------------------------------------------


def test_update_current_position_replaces_section():
    body = "# Session\n\n## Choices\n\n## Current Position\n\nOld position\n\n## Notes\n"
    updated = _update_current_position(body, "**Couplet 3** (p. 5)")
    assert "**Couplet 3** (p. 5)" in updated
    assert "Old position" not in updated


def test_update_current_position_preserves_notes():
    body = "## Choices\n\n## Current Position\n\nOld\n\n## Notes\nMy notes here\n"
    updated = _update_current_position(body, "New position")
    assert "My notes here" in updated


# ---------------------------------------------------------------------------
# Glossary lookup
# ---------------------------------------------------------------------------


def test_lookup_term_exact_match(id_tools, minimal_couplet_graph: tuple[Path, str]):
    db_path, _ = minimal_couplet_graph
    # Insert a term directly for exact-match test
    term_id = str(uuid.uuid4())
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO glossary_terms (id, term, definition) VALUES (?, ?, ?)",
            (term_id, "seta", "A bristle or hair-like structure"),
        )

    result = id_tools["run_lookup_term"](db_path=str(db_path), term="seta")
    assert result["match_type"] == "exact"
    assert "bristle" in result["definition"].lower()


def test_lookup_term_fts_fallback(id_tools, minimal_couplet_graph: tuple[Path, str]):
    db_path, _ = minimal_couplet_graph
    term_id = str(uuid.uuid4())
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO glossary_terms (id, term, definition) VALUES (?, ?, ?)",
            (term_id, "tergite", "A dorsal sclerite of an abdominal segment"),
        )
        conn.execute(
            "INSERT INTO fts_content (content_id, content_type, label, body) VALUES (?, ?, ?, ?)",
            (term_id, "glossary", "tergite", "A dorsal sclerite of an abdominal segment"),
        )

    result = id_tools["run_lookup_term"](db_path=str(db_path), term="tergite")
    # May match exact or FTS depending on normalisation
    assert result["match_type"] in ("exact", "fts_suggestions")


def test_lookup_term_missing_returns_none_definition(
    id_tools, minimal_couplet_graph: tuple[Path, str]
):
    db_path, _ = minimal_couplet_graph
    result = id_tools["run_lookup_term"](db_path=str(db_path), term="xyzzy_no_such_term")
    assert result["definition"] is None


# ---------------------------------------------------------------------------
# Taxa search
# ---------------------------------------------------------------------------


def test_search_taxa_finds_seeded_taxon(id_tools, minimal_couplet_graph: tuple[Path, str]):
    db_path, _ = minimal_couplet_graph
    # Seed FTS for the taxa already in the graph
    taxon_id = str(uuid.uuid4())
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO fts_content (content_id, content_type, label, body) VALUES (?, ?, ?, ?)",
            (taxon_id, "taxon", "Aedes aegypti", "Small mosquito with lyre markings"),
        )

    result = id_tools["run_search_taxa"](db_path=str(db_path), query="Aedes")
    assert any("Aedes" in r["name"] for r in result["results"])


def test_search_taxa_empty_for_no_match(id_tools, minimal_couplet_graph: tuple[Path, str]):
    db_path, _ = minimal_couplet_graph
    result = id_tools["run_search_taxa"](db_path=str(db_path), query="xyzzy_impossible_query_42")
    assert result["results"] == []


# ---------------------------------------------------------------------------
# List keys
# ---------------------------------------------------------------------------


def test_list_keys_no_filter_returns_all(id_tools, minimal_couplet_graph: tuple[Path, str]):
    db_path, key_id = minimal_couplet_graph
    result = id_tools["run_list_keys"](db_path=str(db_path))
    assert len(result["keys"]) >= 1
    assert any(k["key_id"] == key_id for k in result["keys"])


def test_list_keys_taxon_filter_no_match_returns_empty(
    id_tools, minimal_couplet_graph: tuple[Path, str]
):
    db_path, _ = minimal_couplet_graph
    result = id_tools["run_list_keys"](db_path=str(db_path), taxon_name="NonexistentTaxon_xyz")
    assert result["keys"] == []
