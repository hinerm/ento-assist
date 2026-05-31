# Copyright 2026 The ento-assist Authors
# SPDX-License-Identifier: MIT

"""PDF extraction utilities for ento-assist.

All operations are transient — nothing is bulk-stored in the database.
The PDF file stays at its original path; pages are read on demand.

Public API:
    register_document(db_path, path, title, source_type) -> doc_id
    get_page_text(db_path, doc_id, page_num) -> str
    render_page_image(db_path, doc_id, page_num, dpi) -> bytes
    crop_region(db_path, doc_id, page_num, bbox, dpi) -> bytes
    get_page_count(db_path, doc_id) -> int
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TypedDict

import fitz  # pymupdf

from ento_assist.db.connection import get_connection


class BBox(TypedDict):
    x0: float
    y0: float
    x1: float
    y1: float


def register_document(
    db_path: str | Path,
    pdf_path: str | Path,
    title: str,
    source_type: str = "pdf",
) -> str:
    """Register a PDF document in the database and return its UUID.

    Only a single metadata row is written — the PDF is not read or stored.

    Args:
        db_path: Path to the SQLite database.
        pdf_path: Absolute path to the PDF file.
        title: Human-readable title for the document.
        source_type: 'pdf' for text-layer PDFs, 'image_pdf' for scanned images.

    Returns:
        The UUID string assigned to this document.

    Raises:
        FileNotFoundError: If pdf_path does not exist.
    """
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc_id = str(uuid.uuid4())
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, title, path, source_type) VALUES (?, ?, ?, ?)",
            (doc_id, title, str(pdf_path), source_type),
        )
    return doc_id


def _open_pdf(db_path: str | Path, doc_id: str) -> fitz.Document:
    """Look up a document's path and return an open fitz.Document."""
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT path FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise ValueError(f"No document with id={doc_id}")
    return fitz.open(row["path"])


def get_page_count(db_path: str | Path, doc_id: str) -> int:
    """Return the total number of pages in the document."""
    with _open_pdf(db_path, doc_id) as pdf:
        return len(pdf)


def get_page_text(db_path: str | Path, doc_id: str, page_num: int) -> str:
    """Extract the text layer from a single page (0-indexed).

    Returns an empty string if the page has no text layer.
    """
    with _open_pdf(db_path, doc_id) as pdf:
        page = pdf[page_num]
        return str(page.get_text("text"))


def render_page_image(
    db_path: str | Path,
    doc_id: str,
    page_num: int,
    dpi: int = 150,
) -> bytes:
    """Render a page to a PNG image and return the raw bytes.

    The bytes are suitable for use as an MCP image response (base64-encode them
    when constructing the response).

    Args:
        db_path: Path to the SQLite database.
        doc_id: Document UUID.
        page_num: 0-indexed page number.
        dpi: Rendering resolution. 150 dpi is adequate for display; use 300 for
             detailed figure inspection.

    Returns:
        PNG image bytes.
    """
    with _open_pdf(db_path, doc_id) as pdf:
        page = pdf[page_num]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return bytes(pix.tobytes("png"))


def crop_region(
    db_path: str | Path,
    doc_id: str,
    page_num: int,
    bbox: BBox,
    dpi: int = 150,
) -> bytes:
    """Crop a rectangular region from a page and return PNG bytes.

    Coordinates are in PDF points (72 points per inch) at the native page scale.
    The bbox is expressed as {"x0": ..., "y0": ..., "x1": ..., "y1": ...}.

    Returns:
        PNG image bytes for the cropped region.
    """
    with _open_pdf(db_path, doc_id) as pdf:
        page = pdf[page_num]
        rect = fitz.Rect(bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"])
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        clip = rect & page.rect  # clamp to page bounds
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        return bytes(pix.tobytes("png"))
