# ento-assist — Copilot Instructions

This workspace implements an LLM-assisted entomological specimen identification
system. The agent connects to the `ento-assist` MCP server, which exposes tools
for building a dichotomous key database from PDF texts and for guiding users
through identification sessions.

See [README.md](../README.md) for full user and developer documentation.

## Architecture Overview

```
src/ento_assist/
  server.py            — FastMCP server entry point; contains SERVER_INSTRUCTIONS
  db/
    schema.sql         — SQLite schema (source of truth)
    connection.py      — Connection manager with FK enforcement + WAL mode
  extraction/
    pdf.py             — Transient PDF operations (pymupdf)
    ocr.py             — surya-ocr fallback for image-only pages
    key_parser.py      — Heuristic couplet parser; returns ProposedKey for review
  tools/
    ingestion.py       — MCP ingestion tools (register → scan → propose → review → commit)
    identification.py  — MCP identification tools (session file + key traversal)
  utils/
    merge.py           — CLI utility to merge two SQLite databases
```

## Key Conventions

- All primary keys are UUID TEXT (for merge-by-ATTACH portability).
- PDFs remain on disk; only the path is stored in `documents.path`.
- Session state lives in user-owned markdown files with YAML frontmatter,
  NOT in the database.
- Figures are stored as PNG BLOBs in `figures.image_data`.
- FTS5 search is available on `fts_content` (taxa + glossary).
- Page numbers are stored 0-indexed in the DB, but all tools accept and return
  1-indexed values for user-facing display.

## Human-in-the-Loop Requirements

The `SERVER_INSTRUCTIONS` string in `server.py` defines mandatory workflow rules.
The agent MUST:
- Show users the extraction preview before committing any key data.
- Wait for explicit user approval before calling `commit_extraction`.
- Wait for explicit user input (A or B) before calling `advance_session`.
- Present the terminal taxon description and ask for user confirmation before
  concluding any identification.

These rules are enforced via the MCP `initialize` response `instructions` field.
They MUST NOT be weakened in any edits to `server.py`.

## Development Notes

- `surya-ocr` imports are lazy (inside functions) to avoid GPU init at startup.
- `_pending_extractions` in `ingestion.py` is process-scoped in-memory state.
  It does not survive server restarts; re-run `propose_key_structure` if needed.
- The key parser is conservative: confidence < 0.7 indicates couplets that need
  manual review before committing.
