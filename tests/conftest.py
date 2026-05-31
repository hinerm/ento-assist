# Copyright 2026 The ento-assist Authors
# SPDX-License-Identifier: MIT

"""Shared pytest fixtures for ento-assist tests."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from ento_assist.db.connection import get_connection, initialize_db

# ---------------------------------------------------------------------------
# Fixture PDF constants (fill in after first confirmed run with sample_key.pdf)
# ---------------------------------------------------------------------------
FIXTURE_PDF_PAGE_COUNT: int | None = 1  # total pages in sample_key.pdf
FIXTURE_PDF_KEY_PAGE: int | None = 1  # 1-indexed page containing the key
FIXTURE_PDF_TERMINAL_TAXA: list[str] = [
    "Protichneumon effigies",
    "Protichneumon grandis grandis",
    "Protichneumon grandis regnatrix",
    "Protichneumon grandis victoriae",
    "Protichneumon polytropos",
]
FIXTURE_PDF_IMAGE_ONLY_PAGE: int | None = None  # 1-indexed image-only page, or None

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def temp_db(tmp_path: Path) -> Path:
    """Create a fresh SQLite database with the full schema applied."""
    db_path = tmp_path / "test.sqlite"
    initialize_db(db_path)
    return db_path


@pytest.fixture()
def minimal_couplet_graph(temp_db: Path) -> tuple[Path, str]:
    """Insert a minimal 5-couplet binary tree into temp_db.

    Tree structure (all legs resolved):
        Couplet 1 → A: Couplet 2, B: Couplet 3
        Couplet 2 → A: Couplet 4, B: Couplet 5
        Couplet 3 → A: TERMINAL "Aedes aegypti", B: TERMINAL "Culex pipiens"
        Couplet 4 → A: TERMINAL "Anopheles gambiae", B: TERMINAL "Culex pipiens" (shared leaf ok)
        Couplet 5 → A: TERMINAL "Aedes aegypti",  B: TERMINAL "Anopheles gambiae"

    Returns:
        (db_path, key_id) — path to the database and the UUID of the inserted key.
    """
    doc_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())

    taxon_aedes = str(uuid.uuid4())
    taxon_culex = str(uuid.uuid4())
    taxon_anopheles = str(uuid.uuid4())

    c1_id = str(uuid.uuid4())
    c2_id = str(uuid.uuid4())
    c3_id = str(uuid.uuid4())
    c4_id = str(uuid.uuid4())
    c5_id = str(uuid.uuid4())

    with get_connection(temp_db) as conn:
        # Document (required FK for key)
        conn.execute(
            "INSERT INTO documents (id, title, path) VALUES (?, ?, ?)",
            (doc_id, "Test Document", "/tmp/test.pdf"),
        )

        # Taxa
        for tid, name in [
            (taxon_aedes, "Aedes aegypti"),
            (taxon_culex, "Culex pipiens"),
            (taxon_anopheles, "Anopheles gambiae"),
        ]:
            conn.execute(
                "INSERT INTO taxa (id, name, rank) VALUES (?, ?, ?)",
                (tid, name, "species"),
            )

        # Key (start_couplet_id set after couplets created)
        conn.execute(
            "INSERT INTO identification_keys (id, doc_id, title) VALUES (?, ?, ?)",
            (key_id, doc_id, "Test Key to Mosquitoes"),
        )

        # Couplets
        for cid, num in [
            (c1_id, "1"),
            (c2_id, "2"),
            (c3_id, "3"),
            (c4_id, "4"),
            (c5_id, "5"),
        ]:
            conn.execute(
                "INSERT INTO couplets (id, key_id, number, page_ref) VALUES (?, ?, ?, ?)",
                (cid, key_id, num, 0),
            )

        # Couplet legs
        legs: list[tuple[str, str, str, str | None, str | None]] = [
            # (couplet_id, label, text, next_couplet_id, terminal_taxon_id)
            (c1_id, "A", "Wings with two distinct lobes", c2_id, None),
            (c1_id, "B", "Wings without distinct lobes", c3_id, None),
            (c2_id, "A", "Palps longer than proboscis", c4_id, None),
            (c2_id, "B", "Palps shorter than proboscis", c5_id, None),
            (c3_id, "A", "Scutellum with three lobes", None, taxon_aedes),
            (c3_id, "B", "Scutellum evenly rounded", None, taxon_culex),
            (c4_id, "A", "Abdomen with pale banding", None, taxon_anopheles),
            (c4_id, "B", "Abdomen uniformly dark", None, taxon_culex),
            (c5_id, "A", "Thorax with white stripe", None, taxon_aedes),
            (c5_id, "B", "Thorax without white stripe", None, taxon_anopheles),
        ]
        for couplet_id, label, text, nxt, term in legs:
            conn.execute(
                "INSERT INTO couplet_legs "
                "(id, couplet_id, leg_label, text, next_couplet_id, terminal_taxon_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), couplet_id, label, text, nxt, term),
            )

        # Set start_couplet_id on the key
        conn.execute(
            "UPDATE identification_keys SET start_couplet_id = ? WHERE id = ?",
            (c1_id, key_id),
        )

    return temp_db, key_id


@pytest.fixture()
def session_file(tmp_path: Path, minimal_couplet_graph: tuple[Path, str]) -> tuple[Path, Path, str]:
    """Create a starter markdown session file pointing at the first couplet.

    Returns:
        (session_path, db_path, key_id)
    """
    from mcp.server.fastmcp import FastMCP

    from ento_assist.tools.identification import register_identification_tools

    db_path, key_id = minimal_couplet_graph
    session_path = tmp_path / "session.md"

    # Retrieve start_couplet_id directly so we can call start_session
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT start_couplet_id FROM identification_keys WHERE id = ?", (key_id,)
        ).fetchone()
    assert row is not None

    # Call start_session directly (bypasses MCP transport)
    mcp = FastMCP("test")
    register_identification_tools(mcp)
    # Access the underlying function via the tool registry
    start_fn = _get_tool_fn(mcp, "start_session")
    start_fn(db_path=str(db_path), key_id=key_id, output_path=str(session_path))

    return session_path, db_path, key_id


# ---------------------------------------------------------------------------
# PDF fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def sample_pdf_path() -> Path:
    """Return path to the fixture PDF, skipping the test if it is absent."""
    path = _FIXTURES_DIR / "sample_key.pdf"
    if not path.exists():
        pytest.skip(
            "tests/fixtures/sample_key.pdf not found. "
            "See tests/fixtures/README.md for instructions."
        )
    return path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_tool_fn(mcp: FastMCP, name: str):  # type: ignore[type-arg]
    """Retrieve the underlying callable for a registered MCP tool by name."""
    # FastMCP stores tools in mcp._tool_manager._tools (internal API)
    # Fall back to iterating if internal structure changes.
    try:
        tool = mcp._tool_manager._tools[name]
        return tool.fn
    except (AttributeError, KeyError):
        pass
    # Alternative: search through the tool list
    for tool in mcp.list_tools():  # type: ignore[attr-defined]
        if tool.name == name:
            return tool.fn  # type: ignore[attr-defined]
    raise RuntimeError(f"Tool '{name}' not found in FastMCP instance")
