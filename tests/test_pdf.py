# Copyright 2026 The ento-assist Authors
# SPDX-License-Identifier: MIT

"""Tests for extraction/pdf.py — PDF I/O operations.

These tests require tests/fixtures/sample_key.pdf to be present.
See tests/fixtures/README.md for instructions.

All tests in this file are automatically skipped when the fixture file is absent.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from ento_assist.db.connection import initialize_db
from ento_assist.extraction.pdf import (
    crop_region,
    get_page_count,
    get_page_text,
    register_document,
    render_page_image,
)

# ---------------------------------------------------------------------------
# NOTE: Update these constants after the first confirmed run
# See conftest.py for FIXTURE_PDF_* placeholders.
# ---------------------------------------------------------------------------
from tests.conftest import (
    FIXTURE_PDF_IMAGE_ONLY_PAGE,
    FIXTURE_PDF_KEY_PAGE,
    FIXTURE_PDF_PAGE_COUNT,
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture()
def registered_doc(tmp_path: Path, sample_pdf_path: Path) -> tuple[Path, str]:
    """Return (db_path, doc_id) with the fixture PDF registered."""
    db_path = tmp_path / "test.sqlite"
    initialize_db(db_path)
    doc_id = register_document(db_path, sample_pdf_path, "Sample Key")
    return db_path, doc_id


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_document_returns_uuid(registered_doc: tuple[Path, str]):
    _, doc_id = registered_doc
    # Must be a valid UUID string (no exception from uuid.UUID)
    uuid.UUID(doc_id)


def test_register_document_creates_db_row(registered_doc: tuple[Path, str]):
    from ento_assist.db.connection import get_connection

    db_path, doc_id = registered_doc
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT id, title FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row is not None
    assert row["title"] == "Sample Key"


def test_register_document_raises_for_missing_file(tmp_path: Path):
    db_path = tmp_path / "test.sqlite"
    initialize_db(db_path)
    with pytest.raises(FileNotFoundError):
        register_document(db_path, tmp_path / "nonexistent.pdf", "Missing")


# ---------------------------------------------------------------------------
# Page count
# ---------------------------------------------------------------------------


def test_get_page_count(registered_doc: tuple[Path, str]):
    db_path, doc_id = registered_doc
    count = get_page_count(db_path, doc_id)
    assert count > 0
    if FIXTURE_PDF_PAGE_COUNT is not None:
        assert count == FIXTURE_PDF_PAGE_COUNT


# ---------------------------------------------------------------------------
# Page text
# ---------------------------------------------------------------------------


def test_get_page_text_text_page_nonempty(registered_doc: tuple[Path, str]):
    db_path, doc_id = registered_doc
    page_idx = (FIXTURE_PDF_KEY_PAGE - 1) if FIXTURE_PDF_KEY_PAGE else 0
    text = get_page_text(db_path, doc_id, page_idx)
    assert isinstance(text, str)
    assert len(text.strip()) > 0


def test_get_page_text_image_only_page_short(registered_doc: tuple[Path, str]):
    if FIXTURE_PDF_IMAGE_ONLY_PAGE is None:
        pytest.skip("FIXTURE_PDF_IMAGE_ONLY_PAGE not configured")
    db_path, doc_id = registered_doc
    text = get_page_text(db_path, doc_id, FIXTURE_PDF_IMAGE_ONLY_PAGE - 1)
    # Image-only pages should have very little or no extractable text
    assert len(text.strip()) < 50


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------


def test_render_page_image_is_png(registered_doc: tuple[Path, str]):
    db_path, doc_id = registered_doc
    page_idx = (FIXTURE_PDF_KEY_PAGE - 1) if FIXTURE_PDF_KEY_PAGE else 0
    data = render_page_image(db_path, doc_id, page_idx)
    assert isinstance(data, bytes)
    assert data[:8] == _PNG_MAGIC


def test_render_page_image_dpi_scaling(registered_doc: tuple[Path, str]):
    """Higher DPI should produce a larger image."""
    db_path, doc_id = registered_doc
    page_idx = 0
    low_dpi = render_page_image(db_path, doc_id, page_idx, dpi=72)
    high_dpi = render_page_image(db_path, doc_id, page_idx, dpi=300)
    assert len(high_dpi) > len(low_dpi)


# ---------------------------------------------------------------------------
# Crop region
# ---------------------------------------------------------------------------


def test_crop_region_returns_png_bytes(registered_doc: tuple[Path, str]):
    db_path, doc_id = registered_doc
    page_idx = 0
    # Use a small centre-of-page bbox (coordinates in PDF points)
    bbox = {"x0": 72.0, "y0": 72.0, "x1": 200.0, "y1": 200.0}
    data = crop_region(db_path, doc_id, page_idx, bbox)
    assert isinstance(data, bytes)
    assert data[:8] == _PNG_MAGIC
