"""Identification MCP tools for ento-assist.

These tools are exposed to the LLM during specimen identification sessions.
Session state is persisted in a local markdown file (not the database) so
the DB remains a shared, impersonal knowledge artifact.

Session file format (YAML frontmatter + markdown body):

---
session_id: <uuid>
key_id: <uuid>
key_title: "Townes 1969 - Subfamily Key"
db_path: /path/to/ento.sqlite
started: 2026-05-31T14:23:00
last_updated: 2026-05-31T15:01:00
current_couplet_id: <uuid>
status: in_progress
terminal_taxon_id: null
---

# Identification Session: <key_title>

## Choices
1. **Couplet 1** (p. 12) → **A** — "Wings present..."

## Current Position
**Couplet 7** (p. 14) — ...

## Notes

"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter  # python-frontmatter

from mcp.server.fastmcp import FastMCP, Image

from ento_assist.db.connection import get_connection


def register_identification_tools(mcp: FastMCP) -> None:
    """Register all identification tools on the given FastMCP server instance."""

    # ------------------------------------------------------------------
    # Key discovery
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "List all identification keys available in a database, optionally filtered by "
            "taxon name. Returns key_id, title, scope, and source document for each key."
        )
    )
    def list_keys(db_path: str, taxon_name: str = "") -> dict[str, Any]:
        """List available identification keys.

        Args:
            db_path: Absolute path to the SQLite database.
            taxon_name: Optional taxon name to filter keys by scope.
        """
        with get_connection(db_path) as conn:
            if taxon_name:
                rows = conn.execute(
                    """
                    SELECT k.id, k.title, t.name AS scope_taxon, d.title AS doc_title
                    FROM identification_keys k
                    LEFT JOIN taxa t ON k.scope_taxon_id = t.id
                    LEFT JOIN documents d ON k.doc_id = d.id
                    WHERE t.name LIKE ?
                    ORDER BY k.title
                    """,
                    (f"%{taxon_name}%",),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT k.id, k.title, t.name AS scope_taxon, d.title AS doc_title
                    FROM identification_keys k
                    LEFT JOIN taxa t ON k.scope_taxon_id = t.id
                    LEFT JOIN documents d ON k.doc_id = d.id
                    ORDER BY k.title
                    """,
                ).fetchall()

        return {
            "keys": [
                {
                    "key_id": row["id"],
                    "title": row["title"],
                    "scope_taxon": row["scope_taxon"] or "(unscoped)",
                    "source_document": row["doc_title"] or "(unknown)",
                }
                for row in rows
            ]
        }

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Start a new identification session for a key. Creates a session markdown file "
            "at output_path and returns the first couplet. "
            "The session file is personal and should NOT be committed to shared databases."
        )
    )
    def start_session(
        db_path: str,
        key_id: str,
        output_path: str,
    ) -> dict[str, Any]:
        """Create a new identification session file and return the first couplet.

        Args:
            db_path: Absolute path to the SQLite database.
            key_id: UUID of the identification key to use.
            output_path: Path where the session markdown file will be created.
        """
        with get_connection(db_path) as conn:
            key_row = conn.execute(
                "SELECT title, start_couplet_id FROM identification_keys WHERE id = ?",
                (key_id,),
            ).fetchone()

        if key_row is None:
            raise ValueError(f"Key not found: {key_id}")

        key_title = key_row["title"]
        start_couplet_id = key_row["start_couplet_id"]
        if not start_couplet_id:
            raise ValueError(f"Key '{key_title}' has no start_couplet_id set.")

        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        metadata = {
            "session_id": session_id,
            "key_id": key_id,
            "key_title": key_title,
            "db_path": str(db_path),
            "started": now,
            "last_updated": now,
            "current_couplet_id": start_couplet_id,
            "status": "in_progress",
            "terminal_taxon_id": None,
        }
        body = (
            f"# Identification Session: {key_title}\n\n"
            "## Choices\n\n"
            "## Current Position\n\n"
            "## Notes\n"
        )
        post = frontmatter.Post(body, **metadata)

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(frontmatter.dumps(post), encoding="utf-8")

        couplet_data = _get_couplet_data(db_path, start_couplet_id)
        return {
            "session_path": str(output),
            "session_id": session_id,
            "message": f"Session started. Session file: {output}",
            **couplet_data,
        }

    @mcp.tool(
        description=(
            "Resume an existing identification session from its markdown file. "
            "Reads the current state from frontmatter and returns the current couplet data."
        )
    )
    def resume_session(session_path: str) -> dict[str, Any]:
        """Resume an identification session.

        Args:
            session_path: Path to the session markdown file.
        """
        post = _load_session(session_path)
        db_path = post.metadata["db_path"]
        couplet_id = post.metadata["current_couplet_id"]
        status = post.metadata.get("status", "in_progress")

        if status == "complete":
            terminal_id = post.metadata.get("terminal_taxon_id")
            return {
                "status": "complete",
                "terminal_taxon_id": terminal_id,
                "message": "This session is already complete. Use get_taxon_description to review the result.",
            }

        couplet_data = _get_couplet_data(db_path, couplet_id)
        return {
            "session_path": session_path,
            "status": "in_progress",
            **couplet_data,
        }

    @mcp.tool(
        description=(
            "Get the current couplet text and any linked figures. "
            "Figures are returned as MCP image responses — they are for the user to view, "
            "not for the LLM to analyze."
        )
    )
    def get_current_couplet(session_path: str) -> dict[str, Any]:
        """Get the current couplet in a session.

        Args:
            session_path: Path to the session markdown file.
        """
        post = _load_session(session_path)
        db_path = post.metadata["db_path"]
        couplet_id = post.metadata["current_couplet_id"]
        return _get_couplet_data(db_path, couplet_id)

    @mcp.tool(
        description=(
            "Advance the session by choosing a leg (A or B). "
            "Updates the session file and returns the next couplet or the terminal taxon. "
            "IMPORTANT: Always use this tool to advance — never infer the next couplet from text."
        )
    )
    def advance_session(session_path: str, leg_label: str) -> dict[str, Any]:
        """Choose a couplet leg and advance to the next step.

        Args:
            session_path: Path to the session markdown file.
            leg_label: 'A' or 'B'.
        """
        leg_label = leg_label.upper()
        if leg_label not in ("A", "B"):
            raise ValueError("leg_label must be 'A' or 'B'")

        post = _load_session(session_path)
        db_path = post.metadata["db_path"]
        couplet_id = post.metadata["current_couplet_id"]

        with get_connection(db_path) as conn:
            couplet_row = conn.execute(
                "SELECT number, page_ref FROM couplets WHERE id = ?", (couplet_id,)
            ).fetchone()
            leg_row = conn.execute(
                "SELECT id, text, next_couplet_id, terminal_taxon_id "
                "FROM couplet_legs WHERE couplet_id = ? AND leg_label = ?",
                (couplet_id, leg_label),
            ).fetchone()

        if leg_row is None:
            raise ValueError(f"Leg {leg_label} not found for couplet {couplet_id}")

        # Append choice to session body
        couplet_num = couplet_row["number"]
        page_ref = (couplet_row["page_ref"] or 0) + 1
        choice_line = (
            f"\n- **Couplet {couplet_num}** (p. {page_ref}) → **{leg_label}** "
            f"— {leg_row['text'][:120]}...\n"
            if len(leg_row["text"]) > 120
            else f"\n- **Couplet {couplet_num}** (p. {page_ref}) → **{leg_label}** — {leg_row['text']}\n"
        )
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Parse and update session body
        choices_marker = "## Choices"
        body = post.content
        if choices_marker in body:
            body = body.replace(choices_marker, choices_marker + choice_line, 1)
        else:
            body += choice_line

        if leg_row["terminal_taxon_id"]:
            # Terminal: session complete
            taxon_id = leg_row["terminal_taxon_id"]
            post.metadata["status"] = "complete"
            post.metadata["terminal_taxon_id"] = taxon_id
            post.metadata["current_couplet_id"] = couplet_id
            post.metadata["last_updated"] = now

            # Update "Current Position" section
            body = _update_current_position(body, f"**TERMINAL TAXON** (taxon_id={taxon_id})")
            post.content = body
            _save_session(session_path, post)

            return {
                "status": "complete",
                "terminal_taxon_id": taxon_id,
                "message": (
                    "Reached terminal taxon. "
                    "Call get_taxon_description to retrieve and present the full description."
                ),
            }

        elif leg_row["next_couplet_id"]:
            next_id = leg_row["next_couplet_id"]
            post.metadata["current_couplet_id"] = next_id
            post.metadata["last_updated"] = now

            with get_connection(db_path) as conn:
                next_couplet = conn.execute(
                    "SELECT number, page_ref FROM couplets WHERE id = ?", (next_id,)
                ).fetchone()

            position_text = (
                f"**Couplet {next_couplet['number']}** (p. {(next_couplet['page_ref'] or 0) + 1})"
                if next_couplet else f"**Couplet (id={next_id})**"
            )
            body = _update_current_position(body, position_text)
            post.content = body
            _save_session(session_path, post)

            couplet_data = _get_couplet_data(db_path, next_id)
            return {"status": "in_progress", **couplet_data}

        else:
            # Leg has no resolution — needs review
            return {
                "status": "unresolved",
                "message": (
                    f"Leg {leg_label} of couplet {couplet_num} has no goto or terminal taxon. "
                    "This may be a data entry error. Please review the key data."
                ),
            }

    @mcp.tool(
        description=(
            "Look ahead N levels from the current couplet to show what terminal taxa are "
            "reachable via each branch. Useful when the user is uncertain which leg to choose."
        )
    )
    def look_ahead(session_path: str, depth: int = 3) -> dict[str, Any]:
        """Traverse both branches to show reachable terminal taxa.

        Args:
            session_path: Path to the session markdown file.
            depth: Number of levels to look ahead (default 3, max 10).
        """
        depth = min(max(1, depth), 10)
        post = _load_session(session_path)
        db_path = post.metadata["db_path"]
        couplet_id = post.metadata["current_couplet_id"]

        results: dict[str, Any] = {}
        for label in ("A", "B"):
            terminals = _collect_terminals(db_path, couplet_id, label, depth)
            results[label] = terminals

        return {
            "current_couplet_id": couplet_id,
            "lookahead_depth": depth,
            "branch_A": results["A"],
            "branch_B": results["B"],
        }

    @mcp.tool(
        description=(
            "Return the full choice history from a session file as a list. "
            "Reads from the markdown body — no database query required."
        )
    )
    def get_session_history(session_path: str) -> dict[str, Any]:
        """Get the breadcrumb history from a session file.

        Args:
            session_path: Path to the session markdown file.
        """
        post = _load_session(session_path)
        return {
            "session_id": post.metadata.get("session_id"),
            "key_title": post.metadata.get("key_title"),
            "status": post.metadata.get("status"),
            "started": post.metadata.get("started"),
            "last_updated": post.metadata.get("last_updated"),
            "body": post.content,
        }

    # ------------------------------------------------------------------
    # Knowledge retrieval
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Look up a morphological or anatomical term in the glossary. "
            "Call this proactively for any technical term in a couplet leg that the user "
            "may not know. Falls back to FTS5 search if exact match not found."
        )
    )
    def lookup_term(db_path: str, term: str) -> dict[str, Any]:
        """Look up a glossary term.

        Args:
            db_path: Absolute path to the SQLite database.
            term: The term to look up (case-insensitive).
        """
        with get_connection(db_path) as conn:
            # Try exact match first
            row = conn.execute(
                "SELECT term, definition, page_ref FROM glossary_terms WHERE term = ?",
                (term.lower().strip(),),
            ).fetchone()

            if row:
                return {
                    "term": row["term"],
                    "definition": row["definition"],
                    "page_ref": (row["page_ref"] or 0) + 1,
                    "match_type": "exact",
                }

            # FTS5 fallback
            fts_rows = conn.execute(
                "SELECT content_id, label, body FROM fts_content "
                "WHERE content_type = 'glossary' AND fts_content MATCH ? LIMIT 5",
                (term,),
            ).fetchall()

            return {
                "term": term,
                "definition": None,
                "match_type": "none" if not fts_rows else "fts_suggestions",
                "suggestions": [
                    {"term": r["label"], "definition": r["body"]} for r in fts_rows
                ],
            }

    @mcp.tool(
        description=(
            "Retrieve a stored figure image and its caption as an MCP image response. "
            "Call proactively for any figure referenced in a couplet leg. "
            "The image is for the user to view — do NOT attempt to analyze it."
        )
    )
    def get_figure(db_path: str, fig_id: str) -> Image:
        """Retrieve a figure image.

        Args:
            db_path: Absolute path to the SQLite database.
            fig_id: UUID of the figure.
        """
        with get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT image_data, caption FROM figures WHERE id = ?", (fig_id,)
            ).fetchone()

        if row is None:
            raise ValueError(f"Figure not found: {fig_id}")

        return Image(data=bytes(row["image_data"]), format="png")

    @mcp.tool(
        description=(
            "Get the full prose description of a taxon. "
            "Always call this at a terminal taxon and present the description to the user "
            "for verification before concluding the identification."
        )
    )
    def get_taxon_description(db_path: str, taxon_id: str) -> dict[str, Any]:
        """Get the full description of a taxon.

        Args:
            db_path: Absolute path to the SQLite database.
            taxon_id: UUID of the taxon.
        """
        with get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT name, rank, description, page_ref FROM taxa WHERE id = ?",
                (taxon_id,),
            ).fetchone()

        if row is None:
            raise ValueError(f"Taxon not found: {taxon_id}")

        return {
            "taxon_id": taxon_id,
            "name": row["name"],
            "rank": row["rank"],
            "description": row["description"] or "(no description stored)",
            "page_ref": (row["page_ref"] or 0) + 1,
        }

    @mcp.tool(
        description=(
            "Full-text search across taxa and descriptions. "
            "Useful for finding a taxon by a partial name or characteristic."
        )
    )
    def search_taxa(db_path: str, query: str) -> dict[str, Any]:
        """Search taxa and descriptions using FTS5.

        Args:
            db_path: Absolute path to the SQLite database.
            query: Search query string.
        """
        with get_connection(db_path) as conn:
            rows = conn.execute(
                "SELECT content_id, label, body FROM fts_content "
                "WHERE content_type = 'taxon' AND fts_content MATCH ? LIMIT 20",
                (query,),
            ).fetchall()

        return {
            "query": query,
            "results": [
                {"taxon_id": r["content_id"], "name": r["label"], "description_snippet": r["body"][:200]}
                for r in rows
            ],
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_session(session_path: str) -> frontmatter.Post:
    path = Path(session_path)
    if not path.exists():
        raise FileNotFoundError(f"Session file not found: {session_path}")
    return frontmatter.load(str(path))


def _save_session(session_path: str, post: frontmatter.Post) -> None:
    Path(session_path).write_text(frontmatter.dumps(post), encoding="utf-8")


def _get_couplet_data(db_path: str, couplet_id: str) -> dict[str, Any]:
    """Fetch couplet + leg data for display, including figure IDs."""
    with get_connection(db_path) as conn:
        couplet = conn.execute(
            "SELECT number, page_ref FROM couplets WHERE id = ?", (couplet_id,)
        ).fetchone()
        legs = conn.execute(
            "SELECT id, leg_label, text, next_couplet_id, terminal_taxon_id "
            "FROM couplet_legs WHERE couplet_id = ? ORDER BY leg_label",
            (couplet_id,),
        ).fetchall()

        # Figures for each leg
        leg_figures: dict[str, list[dict]] = {}
        for leg in legs:
            figs = conn.execute(
                "SELECT clf.figure_id, clf.reference_text "
                "FROM couplet_leg_figures clf WHERE clf.leg_id = ?",
                (leg["id"],),
            ).fetchall()
            leg_figures[leg["leg_label"]] = [
                {"fig_id": f["figure_id"], "ref_text": f["reference_text"]}
                for f in figs
            ]

    if couplet is None:
        raise ValueError(f"Couplet not found: {couplet_id}")

    result: dict[str, Any] = {
        "couplet_id": couplet_id,
        "couplet_number": couplet["number"],
        "page_ref": (couplet["page_ref"] or 0) + 1,
    }

    for leg in legs:
        label = leg["leg_label"]
        result[f"leg_{label}"] = {
            "leg_id": leg["id"],
            "text": leg["text"],
            "goto_couplet_id": leg["next_couplet_id"],
            "terminal_taxon_id": leg["terminal_taxon_id"],
            "figures": leg_figures.get(label, []),
        }

    return result


def _collect_terminals(
    db_path: str,
    couplet_id: str,
    start_leg: str,
    depth: int,
) -> list[dict[str, Any]]:
    """BFS/DFS to collect terminal taxa reachable from a given leg."""
    terminals: list[dict[str, Any]] = []
    queue: list[tuple[str, str, int]] = [(couplet_id, start_leg, 0)]
    visited: set[str] = set()

    with get_connection(db_path) as conn:
        while queue:
            cur_couplet_id, leg_label, cur_depth = queue.pop(0)
            key = f"{cur_couplet_id}:{leg_label}"
            if key in visited or cur_depth > depth:
                continue
            visited.add(key)

            leg = conn.execute(
                "SELECT next_couplet_id, terminal_taxon_id, text "
                "FROM couplet_legs WHERE couplet_id = ? AND leg_label = ?",
                (cur_couplet_id, leg_label),
            ).fetchone()

            if leg is None:
                continue

            if leg["terminal_taxon_id"]:
                taxon = conn.execute(
                    "SELECT name, rank FROM taxa WHERE id = ?",
                    (leg["terminal_taxon_id"],),
                ).fetchone()
                terminals.append({
                    "taxon_id": leg["terminal_taxon_id"],
                    "name": taxon["name"] if taxon else "(unknown)",
                    "rank": taxon["rank"] if taxon else "(unknown)",
                    "depth": cur_depth,
                })
            elif leg["next_couplet_id"] and cur_depth < depth:
                next_id = leg["next_couplet_id"]
                queue.append((next_id, "A", cur_depth + 1))
                queue.append((next_id, "B", cur_depth + 1))

    return terminals


def _update_current_position(body: str, position_text: str) -> str:
    """Replace the content under '## Current Position' in the session body."""
    marker = "## Current Position"
    next_section = "## Notes"
    if marker in body:
        start = body.index(marker) + len(marker)
        end = body.index(next_section) if next_section in body else len(body)
        body = body[:start] + f"\n{position_text}\n\n" + body[end:]
    return body
