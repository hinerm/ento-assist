# Copyright 2026 The ento-assist Authors
# SPDX-License-Identifier: MIT

"""Integration tests for the ingestion workflow (tools/ingestion.py).

These tests require tests/fixtures/sample_key.pdf to be present.
See tests/fixtures/README.md for instructions.

All tests in this file are automatically skipped when the fixture file is absent.

The tests call ingestion tool functions directly as regular Python — no MCP
transport is involved. The `ctx` parameter (if present) is not needed for
these tools since they use the db_path closure rather than context.

Initial assertions are "shape" tests (range / structure checks). After the
first confirmed run with your fixture PDF, replace the loose bounds with
exact expected values.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from ento_assist.db.connection import get_connection
from ento_assist.tools.ingestion import _pending_extractions, register_ingestion_tools

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    pytest.skip("mcp not installed", allow_module_level=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ingestion_tools(tmp_path: Path, sample_pdf_path: Path):
    """Return a namespace of ingestion tool functions bound to a temp DB."""
    db_path = tmp_path / "ento.sqlite"
    mcp = FastMCP("test-ingestion")
    register_ingestion_tools(mcp, db_path)

    tools = {}
    for tool in mcp._tool_manager._tools.values():
        tools[tool.name] = tool.fn

    # Expose db_path for direct DB assertions
    tools["_db_path"] = db_path
    return tools


# ---------------------------------------------------------------------------
# Sample couplet structure for submit_key_structure tests
# ---------------------------------------------------------------------------

_SAMPLE_COUPLETS = [
    {
        "number": "1",
        "page": 1,
        "leg_a": {"text": "Legs III black.", "goto": "2", "terminal": None, "figures": []},
        "leg_b": {
            "text": "Legs III red.",
            "goto": None,
            "terminal": "Protichneumon polytropos",
            "figures": [],
        },
    },
    {
        "number": "2",
        "page": 1,
        "leg_a": {
            "text": "Scopa large.",
            "goto": None,
            "terminal": "Protichneumon effigies",
            "figures": [],
        },
        "leg_b": {
            "text": "Scopa small.",
            "goto": None,
            "terminal": "Protichneumon grandis grandis",
            "figures": [],
        },
    },
]


# ---------------------------------------------------------------------------
# Document registration
# ---------------------------------------------------------------------------


def test_ingest_document_returns_doc_id(ingestion_tools, sample_pdf_path: Path):
    result = ingestion_tools["ingest_document"](path=str(sample_pdf_path), title="Test Key")
    doc_id = result["doc_id"]
    uuid.UUID(doc_id)  # Must be valid UUID


def test_ingest_document_page_count_positive(ingestion_tools, sample_pdf_path: Path):
    result = ingestion_tools["ingest_document"](path=str(sample_pdf_path), title="Test Key")
    assert int(result["page_count"]) > 0


def test_ingest_document_creates_documents_row(ingestion_tools, sample_pdf_path: Path):
    db_path = ingestion_tools["_db_path"]
    result = ingestion_tools["ingest_document"](path=str(sample_pdf_path), title="Test Key")
    doc_id = result["doc_id"]
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT title FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row is not None
    assert row["title"] == "Test Key"


# ---------------------------------------------------------------------------
# Document structure scan
# ---------------------------------------------------------------------------


def test_scan_document_returns_structure(ingestion_tools, sample_pdf_path: Path):
    result_reg = ingestion_tools["ingest_document"](path=str(sample_pdf_path), title="Scan Test")
    doc_id = result_reg["doc_id"]

    result_scan = ingestion_tools["scan_document_structure"](doc_id=doc_id)
    assert "page_count" in result_scan
    assert "detected_regions" in result_scan
    assert isinstance(result_scan["detected_regions"], list)


def test_scan_document_page_count_matches_registration(ingestion_tools, sample_pdf_path: Path):
    result_reg = ingestion_tools["ingest_document"](path=str(sample_pdf_path), title="Scan Test")
    doc_id = result_reg["doc_id"]
    page_count_reg = int(result_reg["page_count"])

    result_scan = ingestion_tools["scan_document_structure"](doc_id=doc_id)
    assert result_scan["page_count"] == page_count_reg


# ---------------------------------------------------------------------------
# Key structure proposal
# ---------------------------------------------------------------------------


def test_propose_key_structure_returns_extraction_id(ingestion_tools, sample_pdf_path: Path):
    from tests.conftest import FIXTURE_PDF_KEY_PAGE

    result_reg = ingestion_tools["ingest_document"](path=str(sample_pdf_path), title="Propose Test")
    doc_id = result_reg["doc_id"]

    page = FIXTURE_PDF_KEY_PAGE if FIXTURE_PDF_KEY_PAGE else 1
    result = ingestion_tools["propose_key_structure"](
        doc_id=doc_id, page_start=page, page_end=page, title="Test Key"
    )
    assert "extraction_id" in result
    uuid.UUID(result["extraction_id"])  # must be valid UUID


def test_propose_key_structure_returns_raw_pages(ingestion_tools, sample_pdf_path: Path):
    from tests.conftest import FIXTURE_PDF_KEY_PAGE

    result_reg = ingestion_tools["ingest_document"](path=str(sample_pdf_path), title="Propose Test")
    doc_id = result_reg["doc_id"]

    page = FIXTURE_PDF_KEY_PAGE if FIXTURE_PDF_KEY_PAGE else 1
    result = ingestion_tools["propose_key_structure"](
        doc_id=doc_id, page_start=page, page_end=page, title="Test Key"
    )
    assert "pages" in result
    assert isinstance(result["pages"], list)
    assert len(result["pages"]) >= 1
    assert "text" in result["pages"][0]
    assert "couplet_count" not in result  # no auto-parsing


# ---------------------------------------------------------------------------
# Extraction preview
# ---------------------------------------------------------------------------


def _propose(ingestion_tools, sample_pdf_path: Path) -> tuple[str, str]:
    """Helper: register + propose; returns (doc_id, extraction_id)."""
    from tests.conftest import FIXTURE_PDF_KEY_PAGE

    result_reg = ingestion_tools["ingest_document"](path=str(sample_pdf_path), title="Preview Test")
    doc_id = result_reg["doc_id"]
    page = FIXTURE_PDF_KEY_PAGE if FIXTURE_PDF_KEY_PAGE else 1
    result = ingestion_tools["propose_key_structure"](
        doc_id=doc_id, page_start=page, page_end=page, title="Preview Key"
    )
    return doc_id, result["extraction_id"]


def _propose_and_submit(ingestion_tools, sample_pdf_path: Path) -> tuple[str, str]:
    """Helper: register + propose + submit; returns (doc_id, extraction_id)."""
    doc_id, extraction_id = _propose(ingestion_tools, sample_pdf_path)
    ingestion_tools["submit_key_structure"](
        extraction_id=extraction_id, couplets=_SAMPLE_COUPLETS, title="Preview Key"
    )
    return doc_id, extraction_id


def test_get_extraction_preview_awaiting_structure(ingestion_tools, sample_pdf_path: Path):
    """Before submit_key_structure, preview returns raw pages."""
    _, extraction_id = _propose(ingestion_tools, sample_pdf_path)
    preview = ingestion_tools["get_extraction_preview"](extraction_id=extraction_id)
    assert preview["status"] == "awaiting_structure"
    assert "pages" in preview


def test_get_extraction_preview_returns_couplets(ingestion_tools, sample_pdf_path: Path):
    _, extraction_id = _propose_and_submit(ingestion_tools, sample_pdf_path)
    preview = ingestion_tools["get_extraction_preview"](extraction_id=extraction_id)
    assert "couplets" in preview
    assert isinstance(preview["couplets"], list)
    assert len(preview["couplets"]) == len(_SAMPLE_COUPLETS)


def test_get_extraction_preview_invalid_id_raises(ingestion_tools, sample_pdf_path: Path):
    with pytest.raises((ValueError, KeyError)):
        ingestion_tools["get_extraction_preview"](extraction_id=str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# submit_key_structure
# ---------------------------------------------------------------------------


def test_submit_key_structure_returns_couplet_count(ingestion_tools, sample_pdf_path: Path):
    _, extraction_id = _propose(ingestion_tools, sample_pdf_path)
    result = ingestion_tools["submit_key_structure"](
        extraction_id=extraction_id, couplets=_SAMPLE_COUPLETS
    )
    assert result["couplet_count"] == len(_SAMPLE_COUPLETS)


def test_submit_key_structure_missing_id_raises(ingestion_tools, sample_pdf_path: Path):
    with pytest.raises((ValueError, KeyError)):
        ingestion_tools["submit_key_structure"](
            extraction_id=str(uuid.uuid4()), couplets=_SAMPLE_COUPLETS
        )


def test_submit_key_structure_stores_leg_texts(ingestion_tools, sample_pdf_path: Path):
    _, extraction_id = _propose(ingestion_tools, sample_pdf_path)
    ingestion_tools["submit_key_structure"](extraction_id=extraction_id, couplets=_SAMPLE_COUPLETS)
    preview = ingestion_tools["get_extraction_preview"](extraction_id=extraction_id)
    first = preview["couplets"][0]
    assert first["leg_A"]["text"] == _SAMPLE_COUPLETS[0]["leg_a"]["text"]
    assert first["leg_B"]["terminal"] == _SAMPLE_COUPLETS[0]["leg_b"]["terminal"]


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------


def test_correct_extraction_updates_lead_text(ingestion_tools, sample_pdf_path: Path):
    _, extraction_id = _propose_and_submit(ingestion_tools, sample_pdf_path)

    first_num = _SAMPLE_COUPLETS[0]["number"]
    correction = [
        {"couplet_number": first_num, "leg": "A", "field": "text", "value": "CORRECTED TEXT"}
    ]
    result = ingestion_tools["correct_extraction"](
        extraction_id=extraction_id, corrections=correction
    )
    assert len(result["applied"]) == 1
    assert result["errors"] == []

    # Preview should reflect the change
    updated = ingestion_tools["get_extraction_preview"](extraction_id=extraction_id)
    first_couplet = next(c for c in updated["couplets"] if c["number"] == first_num)
    assert first_couplet["leg_A"]["text"] == "CORRECTED TEXT"


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


def test_commit_extraction_writes_to_db(ingestion_tools, sample_pdf_path: Path):
    db_path = ingestion_tools["_db_path"]
    _, extraction_id = _propose_and_submit(ingestion_tools, sample_pdf_path)

    result = ingestion_tools["commit_extraction"](extraction_id=extraction_id)
    key_id = result["key_id"]

    with get_connection(db_path) as conn:
        db_count = conn.execute(
            "SELECT COUNT(*) FROM couplets WHERE key_id = ?", (key_id,)
        ).fetchone()[0]

    assert db_count == len(_SAMPLE_COUPLETS)


def test_commit_extraction_without_submit_raises(ingestion_tools, sample_pdf_path: Path):
    """commit_extraction before submit_key_structure must raise."""
    _, extraction_id = _propose(ingestion_tools, sample_pdf_path)
    with pytest.raises(ValueError, match="submit_key_structure"):
        ingestion_tools["commit_extraction"](extraction_id=extraction_id)


def test_commit_extraction_removes_from_pending(ingestion_tools, sample_pdf_path: Path):
    _, extraction_id = _propose_and_submit(ingestion_tools, sample_pdf_path)
    ingestion_tools["commit_extraction"](extraction_id=extraction_id)
    assert extraction_id not in _pending_extractions


def test_commit_invalid_id_raises(ingestion_tools, sample_pdf_path: Path):
    with pytest.raises((ValueError, KeyError)):
        ingestion_tools["commit_extraction"](extraction_id=str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Glossary
# ---------------------------------------------------------------------------


def test_add_glossary_term_queryable_via_fts(ingestion_tools, sample_pdf_path: Path):
    db_path = ingestion_tools["_db_path"]

    result_reg = ingestion_tools["ingest_document"](
        path=str(sample_pdf_path), title="Glossary Test"
    )
    doc_id = result_reg["doc_id"]

    ingestion_tools["add_glossary_term"](
        term="scutellum",
        definition="The posterior portion of the mesonotum",
        doc_id=doc_id,
        page_ref=1,
    )

    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT label FROM fts_content WHERE fts_content MATCH ?", ("scutellum",)
        ).fetchall()
    assert any(row["label"] == "scutellum" for row in rows)


# ---------------------------------------------------------------------------
# Taxon description
# ---------------------------------------------------------------------------


def test_add_taxon_description_creates_row(ingestion_tools, sample_pdf_path: Path):
    db_path = ingestion_tools["_db_path"]

    result_reg = ingestion_tools["ingest_document"](path=str(sample_pdf_path), title="Taxon Test")
    doc_id = result_reg["doc_id"]

    ingestion_tools["add_taxon_description"](
        taxon_name="Aedes aegypti",
        rank="species",
        description="Small mosquito with characteristic lyre-shaped markings",
        doc_id=doc_id,
        page_ref=1,
    )

    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id, description FROM taxa WHERE name = ?", ("Aedes aegypti",)
        ).fetchone()
    assert row is not None
    assert "mosquito" in (row["description"] or "")
