"""Ingestion MCP tools for ento-assist.

These tools are exposed to the LLM during document ingestion sessions.
They implement the extract → review → correct → commit workflow.

Pending extractions are held in an in-memory store keyed by extraction_id.
They are intentionally NOT persisted — if the server restarts mid-ingestion,
the extraction must be re-run (which is fast and deterministic).
"""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP, Image

from ento_assist.db.connection import get_connection, initialize_db
from ento_assist.extraction.key_parser import (
    ProposedKey,
    parse_key_from_texts,
    scan_for_key_boundaries,
)
from ento_assist.extraction.ocr import is_image_only, run_ocr
from ento_assist.extraction.pdf import (
    crop_region,
    get_page_count,
    get_page_text,
    register_document,
    render_page_image,
)

# In-memory store for pending (not-yet-committed) extractions
# { extraction_id: {"doc_id": ..., "db_path": ..., "proposed_key": ProposedKey} }
_pending_extractions: dict[str, dict[str, Any]] = {}


def register_ingestion_tools(mcp: FastMCP, db_path: str | Path) -> None:
    """Register all ingestion tools on the given FastMCP server instance."""

    db_path = Path(db_path)
    initialize_db(db_path)

    # ------------------------------------------------------------------
    # Document registration
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Register a PDF document in the database. Stores only a single metadata row "
            "(title, path, source type) — the PDF is not read or modified. "
            "Returns the doc_id UUID. ALWAYS call scan_document_structure next."
        )
    )
    def ingest_document(path: str, title: str, source_type: str = "pdf") -> dict[str, str]:
        """Register a PDF document.

        Args:
            path: Absolute path to the PDF file.
            title: Human-readable title (e.g. author + year + volume).
            source_type: 'pdf' for born-digital/text-layer PDFs,
                         'image_pdf' for scanned documents without a text layer.
        """
        doc_id = register_document(db_path, path, title, source_type)
        page_count = get_page_count(db_path, doc_id)
        return {
            "doc_id": doc_id,
            "title": title,
            "page_count": str(page_count),
            "message": f"Document registered. Call scan_document_structure('{doc_id}') next.",
        }

    # ------------------------------------------------------------------
    # Document structure scanning
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Scan all pages of a document to produce a table of contents: detected keys, "
            "page ranges, and section headings. Present results to the user for confirmation "
            "before calling propose_key_structure on any region."
        )
    )
    def scan_document_structure(doc_id: str) -> dict[str, Any]:
        """Scan a document and return its detected structure.

        Args:
            doc_id: UUID of the registered document.
        """
        page_count = get_page_count(db_path, doc_id)
        page_texts = []
        for i in range(page_count):
            text = get_page_text(db_path, doc_id, i)
            if is_image_only(db_path, doc_id, i):
                text = f"[IMAGE-ONLY PAGE — OCR REQUIRED]\n{text}"
            page_texts.append(text)

        regions = scan_for_key_boundaries(page_texts, page_start=0)
        return {
            "doc_id": doc_id,
            "page_count": page_count,
            "detected_regions": [
                {
                    "title": r.title,
                    "page_start": r.page_start + 1,  # 1-indexed for display
                    "page_end": r.page_end + 1,
                    "confidence": round(r.confidence, 2),
                }
                for r in regions
            ],
            "message": (
                "Review the detected regions with the user. Confirm or correct page "
                "boundaries before calling propose_key_structure."
            ),
        }

    # ------------------------------------------------------------------
    # Page access
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Return the text content of a single page for the LLM to read and parse. "
            "Pages are 1-indexed for consistency with printed page numbers shown to the user. "
            "If the result starts with [IMAGE-ONLY], call run_page_ocr instead."
        )
    )
    def get_page_text_tool(doc_id: str, page_num: int) -> dict[str, Any]:
        """Get the text content of a page.

        Args:
            doc_id: Document UUID.
            page_num: 1-indexed page number.
        """
        idx = page_num - 1  # convert to 0-indexed
        text = get_page_text(db_path, doc_id, idx)
        image_only = is_image_only(db_path, doc_id, idx)
        return {
            "doc_id": doc_id,
            "page_num": page_num,
            "image_only": image_only,
            "text": text if not image_only else "[IMAGE-ONLY PAGE — use run_page_ocr]",
        }

    @mcp.tool(
        description=(
            "Run OCR on a single image-only page and return the extracted text. "
            "Use only when get_page_text_tool returns image_only=true. "
            "Results are transient — not stored in the database."
        )
    )
    def run_page_ocr(doc_id: str, page_num: int) -> dict[str, Any]:
        """Run OCR on an image-only page.

        Args:
            doc_id: Document UUID.
            page_num: 1-indexed page number.
        """
        idx = page_num - 1
        text = run_ocr(db_path, doc_id, idx)
        return {"doc_id": doc_id, "page_num": page_num, "text": text}

    @mcp.tool(
        description=(
            "Render a page as an image for display to the user. "
            "The LLM should NOT attempt to analyze this image — it is for the human operator. "
            "Use this when the user needs to visually inspect a page to identify inline figures "
            "or verify couplet text."
        )
    )
    def get_page_image(doc_id: str, page_num: int, dpi: int = 150) -> Image:
        """Render a page image for the user to view.

        Args:
            doc_id: Document UUID.
            page_num: 1-indexed page number.
            dpi: Rendering resolution (150 = screen quality, 300 = detail inspection).
        """
        idx = page_num - 1
        png_bytes = render_page_image(db_path, doc_id, idx, dpi=dpi)
        return Image(data=png_bytes, format="png")

    # ------------------------------------------------------------------
    # Key structure extraction
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Parse a page range into a proposed key structure. "
            "PRECONDITION: scan_document_structure has been reviewed and page boundaries "
            "confirmed with the user. Returns an extraction_id for the pending extraction. "
            "Always call get_extraction_preview next and show the user the results."
        )
    )
    def propose_key_structure(
        doc_id: str,
        page_start: int,
        page_end: int,
        title: str = "",
    ) -> dict[str, Any]:
        """Parse pages into a proposed couplet graph for review.

        Args:
            doc_id: Document UUID.
            page_start: First page of the key (1-indexed).
            page_end: Last page of the key (1-indexed).
            title: Key title (e.g. "Key to Subfamilies of Ichneumonidae").
        """
        start_idx = page_start - 1
        end_idx = page_end - 1

        page_texts = []
        for i in range(start_idx, end_idx + 1):
            text = get_page_text(db_path, doc_id, i)
            if is_image_only(db_path, doc_id, i):
                text = run_ocr(db_path, doc_id, i)
            page_texts.append(text)

        proposed = parse_key_from_texts(page_texts, page_start=start_idx, title=title)

        extraction_id = str(uuid.uuid4())
        _pending_extractions[extraction_id] = {
            "doc_id": doc_id,
            "db_path": str(db_path),
            "proposed_key": proposed,
        }

        return {
            "extraction_id": extraction_id,
            "title": proposed.title,
            "couplet_count": len(proposed.couplets),
            "overall_confidence": round(proposed.confidence, 2),
            "warning_count": len(proposed.warnings),
            "warnings": proposed.warnings,
            "message": f"Call get_extraction_preview('{extraction_id}') to review couplets.",
        }

    @mcp.tool(
        description=(
            "Return a human-readable preview of a pending extraction for spot-checking. "
            "Show the couplets and page references to the user. "
            "Low-confidence couplets (< 0.7) should be highlighted for careful review. "
            "Call correct_extraction if corrections are needed, then commit_extraction."
        )
    )
    def get_extraction_preview(extraction_id: str) -> dict[str, Any]:
        """Get a reviewable preview of a pending extraction.

        Args:
            extraction_id: UUID returned by propose_key_structure.
        """
        entry = _pending_extractions.get(extraction_id)
        if entry is None:
            raise ValueError(f"No pending extraction with id={extraction_id}")

        proposed: ProposedKey = entry["proposed_key"]
        couplet_previews = []
        for c in proposed.couplets:
            couplet_previews.append({
                "number": c.number,
                "page": c.page_ref + 1,  # 1-indexed
                "confidence": round(c.confidence, 2),
                "leg_A": {
                    "text": c.leg_a.text[:200],
                    "goto": c.leg_a.next_couplet_number,
                    "terminal": c.leg_a.terminal_taxon_name,
                    "figures": c.leg_a.figure_references,
                    "confidence": round(c.leg_a.confidence, 2),
                },
                "leg_B": {
                    "text": c.leg_b.text[:200],
                    "goto": c.leg_b.next_couplet_number,
                    "terminal": c.leg_b.terminal_taxon_name,
                    "figures": c.leg_b.figure_references,
                    "confidence": round(c.leg_b.confidence, 2),
                },
            })
        return {
            "extraction_id": extraction_id,
            "title": proposed.title,
            "page_start": proposed.page_start + 1,
            "page_end": proposed.page_end + 1,
            "warnings": proposed.warnings,
            "couplets": couplet_previews,
        }

    @mcp.tool(
        description=(
            "Apply corrections to a pending extraction before committing. "
            "Each correction specifies a couplet number and the field to fix. "
            "Can be called multiple times before commit_extraction."
        )
    )
    def correct_extraction(
        extraction_id: str,
        corrections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Apply corrections to a pending extraction.

        Args:
            extraction_id: UUID returned by propose_key_structure.
            corrections: List of correction dicts. Each must have:
                - "couplet_number": str (e.g. "3")
                - "leg": "A" or "B"
                - "field": one of "text", "goto", "terminal", "figures"
                - "value": new value (str, or list[str] for figures)

        Example:
            [{"couplet_number": "3", "leg": "A", "field": "goto", "value": "5"}]
        """
        entry = _pending_extractions.get(extraction_id)
        if entry is None:
            raise ValueError(f"No pending extraction with id={extraction_id}")

        proposed: ProposedKey = entry["proposed_key"]
        applied = []
        errors = []

        couplet_map = {c.number: c for c in proposed.couplets}

        for corr in corrections:
            num = str(corr.get("couplet_number", ""))
            leg_label = str(corr.get("leg", "")).upper()
            field = str(corr.get("field", ""))
            value = corr.get("value")

            couplet = couplet_map.get(num)
            if couplet is None:
                errors.append(f"Couplet {num} not found.")
                continue

            leg = couplet.leg_a if leg_label == "A" else couplet.leg_b
            if field == "text":
                leg.text = str(value)
                leg.confidence = 1.0
            elif field == "goto":
                leg.next_couplet_number = str(value) if value else None
                leg.terminal_taxon_name = None
                leg.confidence = 1.0
            elif field == "terminal":
                leg.terminal_taxon_name = str(value) if value else None
                leg.next_couplet_number = None
                leg.confidence = 1.0
            elif field == "figures":
                leg.figure_references = list(value) if value else []
            else:
                errors.append(f"Unknown field '{field}' for couplet {num} leg {leg_label}.")
                continue

            applied.append(f"Couplet {num} leg {leg_label}.{field} updated.")

        return {"applied": applied, "errors": errors}

    @mcp.tool(
        description=(
            "Commit a reviewed and corrected extraction to the database. "
            "PRECONDITION: get_extraction_preview has been shown to the user and they "
            "have explicitly approved the extraction. Do NOT call this without user approval. "
            "Returns the key_id and a summary of committed couplets."
        )
    )
    def commit_extraction(extraction_id: str) -> dict[str, Any]:
        """Write a validated couplet graph to the database.

        Args:
            extraction_id: UUID returned by propose_key_structure.
        """
        entry = _pending_extractions.get(extraction_id)
        if entry is None:
            raise ValueError(f"No pending extraction with id={extraction_id}")

        proposed: ProposedKey = entry["proposed_key"]
        doc_id: str = entry["doc_id"]

        key_id = str(uuid.uuid4())
        couplet_id_map: dict[str, str] = {}  # number → UUID
        leg_id_map: dict[tuple[str, str], str] = {}  # (couplet_num, leg_label) → UUID

        # First pass: assign UUIDs to all couplets and legs
        for c in proposed.couplets:
            couplet_id_map[c.number] = str(uuid.uuid4())
            leg_id_map[(c.number, "A")] = str(uuid.uuid4())
            leg_id_map[(c.number, "B")] = str(uuid.uuid4())

        with get_connection(db_path) as conn:
            # Insert identification key (start_couplet_id set after)
            start_num = proposed.couplets[0].number if proposed.couplets else None
            conn.execute(
                "INSERT INTO identification_keys (id, doc_id, title, scope_taxon_id, start_couplet_id) "
                "VALUES (?, ?, ?, NULL, ?)",
                (key_id, doc_id, proposed.title, couplet_id_map.get(start_num) if start_num else None),
            )

            for c in proposed.couplets:
                couplet_db_id = couplet_id_map[c.number]

                conn.execute(
                    "INSERT INTO couplets (id, key_id, number, page_ref) VALUES (?, ?, ?, ?)",
                    (couplet_db_id, key_id, c.number, c.page_ref),
                )

                for leg, label in [(c.leg_a, "A"), (c.leg_b, "B")]:
                    leg_db_id = leg_id_map[(c.number, label)]
                    next_couplet_db_id = (
                        couplet_id_map.get(leg.next_couplet_number)
                        if leg.next_couplet_number
                        else None
                    )
                    conn.execute(
                        "INSERT INTO couplet_legs "
                        "(id, couplet_id, leg_label, text, next_couplet_id, terminal_taxon_id) "
                        "VALUES (?, ?, ?, ?, ?, NULL)",
                        (leg_db_id, couplet_db_id, label, leg.text, next_couplet_db_id),
                    )

        del _pending_extractions[extraction_id]

        return {
            "key_id": key_id,
            "title": proposed.title,
            "couplets_committed": len(proposed.couplets),
            "message": (
                f"Key '{proposed.title}' committed with {len(proposed.couplets)} couplets. "
                f"key_id={key_id}. You can now link figures or add taxon descriptions."
            ),
        }

    # ------------------------------------------------------------------
    # Figure handling
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Crop a rectangular region from a page and store it as a figure BLOB. "
            "Coordinates are in PDF points (72pt = 1 inch) at the native page scale. "
            "Use after the user has described a figure's location from the displayed page image. "
            "Returns fig_id. Call link_figure to associate with a couplet leg."
        )
    )
    def crop_figure(
        doc_id: str,
        page_num: int,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        caption: str = "",
    ) -> dict[str, str]:
        """Crop a figure from a page and store it.

        Args:
            doc_id: Document UUID.
            page_num: 1-indexed page number.
            x0, y0, x1, y1: Bounding box in PDF points (origin = top-left).
            caption: Figure caption or description (may be empty for unlabeled figures).
        """
        idx = page_num - 1
        bbox = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
        png_bytes = crop_region(db_path, doc_id, idx, bbox)
        fig_id = str(uuid.uuid4())

        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO figures (id, doc_id, page_num, caption, image_data, bbox) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (fig_id, doc_id, idx, caption, png_bytes, json.dumps(bbox)),
            )

        return {
            "fig_id": fig_id,
            "message": f"Figure stored. Call link_figure('{fig_id}', leg_id, ref_text) to associate it.",
        }

    @mcp.tool(
        description=(
            "Associate a stored figure with a couplet leg. "
            "ref_text is the original in-text reference (e.g. 'Fig. 3b') or empty string "
            "for inline/unlabeled figures identified interactively with the user."
        )
    )
    def link_figure(fig_id: str, leg_id: str, ref_text: str = "") -> dict[str, str]:
        """Link a figure to a couplet leg.

        Args:
            fig_id: UUID of the stored figure.
            leg_id: UUID of the couplet leg.
            ref_text: Original figure reference text, or '' for unlabeled figures.
        """
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO couplet_leg_figures (leg_id, figure_id, reference_text) "
                "VALUES (?, ?, ?)",
                (leg_id, fig_id, ref_text or None),
            )
        return {"message": f"Figure {fig_id} linked to leg {leg_id}."}

    # ------------------------------------------------------------------
    # Glossary and taxon descriptions
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Add a morphological or anatomical term and its definition to the glossary. "
            "If the term already exists, the call is silently ignored (no overwrite). "
            "Use add_taxon_description for taxon-level prose descriptions."
        )
    )
    def add_glossary_term(
        term: str,
        definition: str,
        doc_id: str,
        page_ref: int,
    ) -> dict[str, str]:
        """Add a glossary term.

        Args:
            term: The morphological/anatomical term.
            definition: Plain-text definition.
            doc_id: Source document UUID.
            page_ref: 1-indexed page number where this term is defined.
        """
        term_id = str(uuid.uuid4())
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO glossary_terms (id, term, definition, doc_id, page_ref) "
                "VALUES (?, ?, ?, ?, ?)",
                (term_id, term.lower().strip(), definition, doc_id, page_ref - 1),
            )
            # Update FTS index
            conn.execute(
                "INSERT INTO fts_content (content_id, content_type, label, body) VALUES (?, ?, ?, ?)",
                (term_id, "glossary", term, definition),
            )
        return {"message": f"Glossary term '{term}' added."}

    @mcp.tool(
        description=(
            "Add or update the prose description for a taxon (subfamily, genus, species, etc.). "
            "The description should be the full taxonomic characterisation from the source text. "
            "Used during identification to verify a terminal taxon match."
        )
    )
    def add_taxon_description(
        taxon_name: str,
        rank: str,
        description: str,
        doc_id: str,
        page_ref: int,
        parent_taxon_name: str = "",
    ) -> dict[str, str]:
        """Add or update a taxon with its description.

        Args:
            taxon_name: Scientific name (e.g. "Ophioninae").
            rank: Taxonomic rank (e.g. "subfamily", "genus", "species").
            description: Full prose description from the source.
            doc_id: Source document UUID.
            page_ref: 1-indexed page number.
            parent_taxon_name: Name of the parent taxon (if known), for hierarchy.
        """
        # Look up parent if provided
        parent_id = None
        if parent_taxon_name:
            with get_connection(db_path) as conn:
                row = conn.execute(
                    "SELECT id FROM taxa WHERE name = ? LIMIT 1",
                    (parent_taxon_name,),
                ).fetchone()
                if row:
                    parent_id = row["id"]

        taxon_id = str(uuid.uuid4())
        with get_connection(db_path) as conn:
            # Upsert: if the taxon already exists, update description
            existing = conn.execute(
                "SELECT id FROM taxa WHERE name = ? AND rank = ?",
                (taxon_name, rank),
            ).fetchone()

            if existing:
                taxon_id = existing["id"]
                conn.execute(
                    "UPDATE taxa SET description = ?, doc_id = ?, page_ref = ? WHERE id = ?",
                    (description, doc_id, page_ref - 1, taxon_id),
                )
            else:
                conn.execute(
                    "INSERT INTO taxa (id, name, rank, parent_id, description, doc_id, page_ref) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (taxon_id, taxon_name, rank, parent_id, description, doc_id, page_ref - 1),
                )
            # Update FTS
            conn.execute(
                "INSERT INTO fts_content (content_id, content_type, label, body) VALUES (?, ?, ?, ?)",
                (taxon_id, "taxon", taxon_name, description),
            )

        return {"taxon_id": taxon_id, "message": f"Taxon '{taxon_name}' ({rank}) saved."}
