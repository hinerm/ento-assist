# Copyright 2026 The ento-assist Authors
# SPDX-License-Identifier: MIT

"""OCR fallback for image-only PDF pages using surya-ocr.

Used when a page has no extractable text layer (e.g. phone-scanned documents
via vFlat). Results are returned transiently — nothing is persisted.

Public API:
    run_ocr(db_path, doc_id, page_num) -> str
    is_image_only(db_path, doc_id, page_num) -> bool
"""

from __future__ import annotations

from pathlib import Path

from ento_assist.extraction.pdf import get_page_text, render_page_image

# Surya is imported lazily to avoid mandatory GPU initialisation at import time.
# Call sites should handle ImportError gracefully if surya is not installed.


_TEXT_THRESHOLD = 50  # characters; pages below this are treated as image-only


def is_image_only(db_path: str | Path, doc_id: str, page_num: int) -> bool:
    """Return True if the page has too little text to rely on the text layer."""
    text = get_page_text(db_path, doc_id, page_num)
    return len(text.strip()) < _TEXT_THRESHOLD


def run_ocr(db_path: str | Path, doc_id: str, page_num: int) -> str:
    """Run surya OCR on a single page and return the extracted text.

    The result is returned as a plain string and is NOT stored in the database.
    Callers (ingestion tools) use this text transiently for parsing.

    Args:
        db_path: Path to the SQLite database (used to locate the document path).
        doc_id: Document UUID.
        page_num: 0-indexed page number.

    Returns:
        Extracted text string.

    Raises:
        ImportError: If surya-ocr is not installed.
        RuntimeError: If OCR fails for any reason.
    """
    try:
        from surya.model.detection.model import load_model as load_det_model
        from surya.model.detection.processor import load_processor as load_det_processor
        from surya.model.recognition.model import load_model as load_rec_model
        from surya.model.recognition.processor import load_processor as load_rec_processor
        from surya.ocr import run_ocr as surya_run_ocr
    except ImportError as exc:
        raise ImportError(
            "surya-ocr is required for OCR on image-only pages. Install it with: uv add surya-ocr"
        ) from exc

    # Render page to image for surya input
    image_bytes = render_page_image(db_path, doc_id, page_num, dpi=300)

    try:
        import io

        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))

        det_processor, det_model = load_det_processor(), load_det_model()
        rec_model, rec_processor = load_rec_model(), load_rec_processor()

        predictions = surya_run_ocr(
            [image],
            [["en"]],
            det_model,
            det_processor,
            rec_model,
            rec_processor,
        )

        # Collect all text lines in reading order
        lines = [line.text for line in predictions[0].text_lines]
        return "\n".join(lines)

    except Exception as exc:
        raise RuntimeError(f"OCR failed for doc={doc_id} page={page_num}: {exc}") from exc
