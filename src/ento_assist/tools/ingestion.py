# Copyright 2026 The ento-assist Authors
# SPDX-License-Identifier: MIT

"""Ingestion MCP tools for ento-assist.

These tools are exposed to the LLM during document ingestion sessions.
They implement a couplet-by-couplet interactive workflow:

  build_register_document  → build_scan_document
  → build_create_key
  → build_add_couplet  (repeated, one couplet at a time with user confirmation)
  → build_finalize_key

Figure capture and glossary/taxon additions can be interleaved at any point.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

from mcp.server.fastmcp import FastMCP, Image

from ento_assist.db.connection import get_connection, initialize_db
from ento_assist.extraction.key_parser import scan_for_key_boundaries
from ento_assist.extraction.ocr import is_image_only, run_ocr
from ento_assist.extraction.pdf import (
    BBox,
    crop_region,
    get_page_count,
    get_page_text,
    register_document,
    render_page_image,
)


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
            "Returns the doc_id UUID. ALWAYS call build_scan_document next."
        )
    )
    def build_register_document(path: str, title: str, source_type: str = "pdf") -> dict[str, str]:
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
            "message": f"Document registered. Call build_scan_document('{doc_id}') next.",
        }

    # ------------------------------------------------------------------
    # Document structure scanning
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Scan all pages of a document to detect likely key regions and page ranges. "
            "Present the results to the user for confirmation and correction before proceeding. "
            "Page boundaries are estimates — the user must confirm or adjust them."
        )
    )
    def build_scan_document(doc_id: str) -> dict[str, Any]:
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
                "boundaries before calling build_create_key."
            ),
        }

    # ------------------------------------------------------------------
    # Page access
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Return the text content of a single page for the LLM to read and parse. "
            "Pages are 1-indexed. If the result starts with [IMAGE-ONLY], "
            "call build_ocr_page instead."
        )
    )
    def build_get_page_text(doc_id: str, page_num: int) -> dict[str, Any]:
        """Get the text content of a page.

        Args:
            doc_id: Document UUID.
            page_num: 1-indexed page number.
        """
        idx = page_num - 1
        text = get_page_text(db_path, doc_id, idx)
        image_only = is_image_only(db_path, doc_id, idx)
        return {
            "doc_id": doc_id,
            "page_num": page_num,
            "image_only": image_only,
            "text": text if not image_only else "[IMAGE-ONLY PAGE — use build_ocr_page]",
        }

    @mcp.tool(
        description=(
            "Run OCR on a single image-only page and return the extracted text. "
            "Use only when build_get_page_text returns image_only=true. "
            "Results are transient — not stored in the database."
        )
    )
    def build_ocr_page(doc_id: str, page_num: int) -> dict[str, Any]:
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
            "or verify couplet text. Use dpi=300 for detailed figure inspection."
        )
    )
    def build_get_page_image(doc_id: str, page_num: int, dpi: int = 150) -> Image:
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
    # Key creation
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Create a new identification key record in the database. "
            "Call this after confirming page boundaries with the user and "
            "before starting the couplet-by-couplet loop. "
            "base_taxon_name is the taxon the key operates on (e.g. 'Ichneumonidae'). "
            "base_taxon_rank is its rank (e.g. 'family'). "
            "leaf_taxon_rank is the rank the terminals resolve to (e.g. 'subfamily'). "
            "The key title is auto-generated as '<base_taxon_name> → <leaf_taxon_rank>'. "
            "Returns key_id to use in subsequent build_add_couplet calls."
        )
    )
    def build_create_key(
        doc_id: str,
        base_taxon_name: str,
        base_taxon_rank: str,
        leaf_taxon_rank: str,
    ) -> dict[str, str]:
        """Create an identification key record.

        Args:
            doc_id: UUID of the source document.
            base_taxon_name: Scientific name of the taxon the key covers
                             (e.g. 'Ichneumonidae').
            base_taxon_rank: Taxonomic rank of the base taxon
                             (e.g. 'family', 'superfamily').
            leaf_taxon_rank: Rank that terminal couplet legs resolve to
                             (e.g. 'subfamily', 'genus', 'species').
        """
        # Look up or create the base taxon
        with get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT id FROM taxa WHERE name = ? AND rank = ? LIMIT 1",
                (base_taxon_name, base_taxon_rank),
            ).fetchone()
            if row:
                taxon_id = cast(str, row["id"])
            else:
                taxon_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO taxa (id, name, rank, parent_id, description, doc_id, page_ref) "
                    "VALUES (?, ?, ?, NULL, NULL, ?, NULL)",
                    (taxon_id, base_taxon_name, base_taxon_rank, doc_id),
                )

            title = f"{base_taxon_name} → {leaf_taxon_rank}"
            key_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO identification_keys "
                "(id, doc_id, title, base_taxon_id, leaf_taxon_rank, start_couplet_id) "
                "VALUES (?, ?, ?, ?, ?, NULL)",
                (key_id, doc_id, title, taxon_id, leaf_taxon_rank),
            )

        return {
            "key_id": key_id,
            "title": title,
            "message": (
                f"Key '{title}' created (key_id={key_id}). "
                "Now call build_add_couplet for each couplet, one at a time, "
                "with user confirmation after each. "
                "Call build_finalize_key when all couplets are added."
            ),
        }

    # ------------------------------------------------------------------
    # Couplet ingestion (incremental)
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Add a single couplet (with both legs) to an existing key. "
            "Call this ONCE PER COUPLET, only after the user has confirmed the "
            "interpretation is correct. "
            "Each leg must have either a goto (next couplet number as a string, e.g. '4') "
            "or a terminal (taxon name string), but NOT both. "
            "leg_x_goto and leg_x_terminal are both optional (pass null/None if not applicable). "
            "If a leg has neither, it is stored as unresolved — a warning is returned. "
            "Returns couplet_id, leg_a_id, and leg_b_id for use with build_link_figure."
        )
    )
    def build_add_couplet(
        key_id: str,
        number: str,
        page_ref: int,
        leg_a_text: str,
        leg_b_text: str,
        leg_a_goto: str | None = None,
        leg_a_terminal: str | None = None,
        leg_b_goto: str | None = None,
        leg_b_terminal: str | None = None,
    ) -> dict[str, Any]:
        """Add a single confirmed couplet to the database.

        Args:
            key_id: UUID of the identification key.
            number: Couplet number/label as it appears in the source (e.g. '1', '1a').
            page_ref: 1-indexed page number where this couplet appears.
            leg_a_text: Full text of leg A (the first / affirmative condition).
            leg_b_text: Full text of leg B (the second / negative condition).
            leg_a_goto: Couplet number leg A leads to (e.g. '4'), or None.
            leg_a_terminal: Taxon name leg A identifies (e.g. 'Ophioninae'), or None.
            leg_b_goto: Couplet number leg B leads to, or None.
            leg_b_terminal: Taxon name leg B identifies, or None.
        """
        warnings: list[str] = []

        def _resolve_terminal(name: str) -> str:
            """Look up or create a taxon stub and return its UUID."""
            with get_connection(db_path) as conn:
                row = conn.execute("SELECT id FROM taxa WHERE name = ? LIMIT 1", (name,)).fetchone()
                if row:
                    return cast(str, row["id"])
                tid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO taxa (id, name, rank, parent_id, description, doc_id, page_ref) "
                    "VALUES (?, ?, 'unknown', NULL, NULL, NULL, NULL)",
                    (tid, name),
                )
                return tid

        # Validate key exists
        with get_connection(db_path) as conn:
            key_row = conn.execute(
                "SELECT id FROM identification_keys WHERE id = ?", (key_id,)
            ).fetchone()
            if not key_row:
                raise ValueError(f"No key found with id={key_id}")

            # Ensure couplet number is unique within this key
            existing = conn.execute(
                "SELECT id FROM couplets WHERE key_id = ? AND number = ?",
                (key_id, number),
            ).fetchone()
            if existing:
                raise ValueError(
                    f"Couplet '{number}' already exists in key {key_id}. "
                    "Use build_correct_leg if you need to update it."
                )

        if leg_a_goto and leg_a_terminal:
            raise ValueError("Leg A: provide either goto or terminal, not both.")
        if leg_b_goto and leg_b_terminal:
            raise ValueError("Leg B: provide either goto or terminal, not both.")

        if not leg_a_goto and not leg_a_terminal:
            warnings.append(f"Couplet {number} leg A: no goto or terminal — stored as unresolved.")
        if not leg_b_goto and not leg_b_terminal:
            warnings.append(f"Couplet {number} leg B: no goto or terminal — stored as unresolved.")

        couplet_id = str(uuid.uuid4())
        leg_a_id = str(uuid.uuid4())
        leg_b_id = str(uuid.uuid4())
        page_0idx = page_ref - 1

        # Resolve terminal taxon UUIDs (may create stub taxa)
        leg_a_taxon_id = _resolve_terminal(leg_a_terminal) if leg_a_terminal else None
        leg_b_taxon_id = _resolve_terminal(leg_b_terminal) if leg_b_terminal else None

        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO couplets (id, key_id, number, page_ref) VALUES (?, ?, ?, ?)",
                (couplet_id, key_id, number, page_0idx),
            )
            conn.execute(
                "INSERT INTO couplet_legs "
                "(id, couplet_id, leg_label, text, next_couplet_number, "
                "next_couplet_id, terminal_taxon_id) "
                "VALUES (?, ?, 'A', ?, ?, NULL, ?)",
                (leg_a_id, couplet_id, leg_a_text, leg_a_goto, leg_a_taxon_id),
            )
            conn.execute(
                "INSERT INTO couplet_legs "
                "(id, couplet_id, leg_label, text, next_couplet_number, "
                "next_couplet_id, terminal_taxon_id) "
                "VALUES (?, ?, 'B', ?, ?, NULL, ?)",
                (leg_b_id, couplet_id, leg_b_text, leg_b_goto, leg_b_taxon_id),
            )

        result: dict[str, Any] = {
            "couplet_id": couplet_id,
            "leg_a_id": leg_a_id,
            "leg_b_id": leg_b_id,
            "message": f"Couplet {number} committed.",
        }
        if warnings:
            result["warnings"] = warnings
        return result

    # ------------------------------------------------------------------
    # Key finalization
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Finalize a key after all couplets have been added. "
            "Resolves all next_couplet_number references to next_couplet_id UUIDs, "
            "and sets the key's start_couplet_id to the couplet numbered '1' "
            "(or the lowest-numbered couplet if '1' is not present). "
            "Returns counts of resolved and unresolved legs. "
            "Unresolved legs (goto number not found) are reported as warnings — "
            "they do not prevent finalization, but should be reviewed."
        )
    )
    def build_finalize_key(key_id: str) -> dict[str, Any]:
        """Resolve forward references and set start_couplet_id.

        Args:
            key_id: UUID of the identification key to finalize.
        """
        with get_connection(db_path) as conn:
            key_row = conn.execute(
                "SELECT id FROM identification_keys WHERE id = ?", (key_id,)
            ).fetchone()
            if not key_row:
                raise ValueError(f"No key found with id={key_id}")

            # Build number → UUID map for all couplets in this key
            couplet_rows = conn.execute(
                "SELECT id, number FROM couplets WHERE key_id = ?", (key_id,)
            ).fetchall()
            number_to_id: dict[str, str] = {row["number"]: row["id"] for row in couplet_rows}

            # Fetch all legs that need resolution
            legs = conn.execute(
                "SELECT cl.id, cl.next_couplet_number "
                "FROM couplet_legs cl "
                "JOIN couplets c ON cl.couplet_id = c.id "
                "WHERE c.key_id = ? AND cl.next_couplet_id IS NULL "
                "AND cl.next_couplet_number IS NOT NULL",
                (key_id,),
            ).fetchall()

            resolved = 0
            unresolved: list[dict[str, str]] = []

            for leg in legs:
                target_id = number_to_id.get(leg["next_couplet_number"])
                if target_id:
                    conn.execute(
                        "UPDATE couplet_legs SET next_couplet_id = ? WHERE id = ?",
                        (target_id, leg["id"]),
                    )
                    resolved += 1
                else:
                    unresolved.append(
                        {
                            "leg_id": leg["id"],
                            "missing_goto": leg["next_couplet_number"],
                        }
                    )

            # Set start_couplet_id: prefer "1", else lowest numeric value
            start_id = number_to_id.get("1")
            if not start_id and number_to_id:

                def _num_sort(item: tuple[str, str]) -> tuple[int, str]:
                    import re

                    m = re.match(r"(\d+)", item[0])
                    return (int(m.group(1)) if m else 0, item[0])

                first_num = sorted(number_to_id.items(), key=_num_sort)[0][1]
                start_id = first_num

            if start_id:
                conn.execute(
                    "UPDATE identification_keys SET start_couplet_id = ? WHERE id = ?",
                    (start_id, key_id),
                )

        result: dict[str, Any] = {
            "key_id": key_id,
            "resolved_count": resolved,
            "message": (
                f"Key finalized. {resolved} forward references resolved. "
                + (
                    f"{len(unresolved)} unresolved gotos — see 'unresolved' list."
                    if unresolved
                    else "All references resolved."
                )
            ),
        }
        if unresolved:
            result["unresolved"] = unresolved
        return result

    # ------------------------------------------------------------------
    # Figure handling
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Crop a rectangular region from a page and store it as a figure BLOB. "
            "Coordinates are in PDF points (72pt = 1 inch) at the native page scale. "
            "Use after the user has described a figure's location from the displayed page image. "
            "Returns fig_id. Call build_link_figure to associate with a couplet leg."
        )
    )
    def build_crop_figure(
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
        bbox: BBox = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
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
            "message": (
                f"Figure stored. "
                f"Call build_link_figure('{fig_id}', leg_id, ref_text) to associate it."
            ),
        }

    @mcp.tool(
        description=(
            "Associate a stored figure with a couplet leg. "
            "ref_text is the original in-text reference (e.g. 'Fig. 3b') or empty string "
            "for inline/unlabeled figures identified interactively with the user."
        )
    )
    def build_link_figure(fig_id: str, leg_id: str, ref_text: str = "") -> dict[str, str]:
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
            "Add a morphological or anatomical term and its definition to the global glossary. "
            "If the term already exists, the call is silently ignored (no overwrite). "
            "Use build_add_taxon for taxon-level prose descriptions."
        )
    )
    def build_add_term(term: str, definition: str) -> dict[str, str]:
        """Add a glossary term.

        Args:
            term: The morphological/anatomical term.
            definition: Plain-text definition.
        """
        term_id = str(uuid.uuid4())
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO glossary_terms (id, term, definition) VALUES (?, ?, ?)",
                (term_id, term.lower().strip(), definition),
            )
            conn.execute(
                "INSERT INTO fts_content"
                " (content_id, content_type, label, body) VALUES (?, ?, ?, ?)",
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
    def build_add_taxon(
        taxon_name: str,
        rank: str,
        description: str,
        doc_id: str,
        page_ref: int,
        parent_taxon_name: str = "",
    ) -> dict[str, str]:
        """Add or update a taxon with its description.

        Args:
            taxon_name: Scientific name (e.g. 'Ophioninae').
            rank: Taxonomic rank (e.g. 'subfamily', 'genus', 'species').
            description: Full prose description from the source.
            doc_id: Source document UUID.
            page_ref: 1-indexed page number.
            parent_taxon_name: Name of the parent taxon (if known), for hierarchy.
        """
        parent_id = None
        if parent_taxon_name:
            with get_connection(db_path) as conn:
                row = conn.execute(
                    "SELECT id FROM taxa WHERE name = ? LIMIT 1",
                    (parent_taxon_name,),
                ).fetchone()
                if row:
                    parent_id = cast(str, row["id"])

        taxon_id = str(uuid.uuid4())
        with get_connection(db_path) as conn:
            existing = conn.execute(
                "SELECT id FROM taxa WHERE name = ? AND rank = ?",
                (taxon_name, rank),
            ).fetchone()

            if existing:
                taxon_id = cast(str, existing["id"])
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
            conn.execute(
                "INSERT INTO fts_content"
                " (content_id, content_type, label, body) VALUES (?, ?, ?, ?)",
                (taxon_id, "taxon", taxon_name, description),
            )

        return {"taxon_id": taxon_id, "message": f"Taxon '{taxon_name}' ({rank}) saved."}
