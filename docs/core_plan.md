# Plan: ento-assist MCP Server

## Summary

Python MCP server backed by a portable SQLite database. Two operational modes exposed as distinct tool groups: **ingestion** (parse documents → human-verified commit to graph) and **identification** (deterministic key traversal with LLM as guide). The LLM orchestrates workflows but never analyzes page images directly — it displays them to the human operator via MCP image responses and acts on user annotations.

---

## Decisions

- **Language**: Python
- **MCP SDK**: `mcp` (official Python SDK)
- **Database**: SQLite — single portable file, UUID PKs for mergeable data
- **PDF handling**: `pymupdf` (fitz) — text extraction + image rendering
- **OCR fallback**: `surya` (modern, handles scientific text) for image-only PDFs (phone scans)
- **Graph model**: adjacency list in SQLite (no separate graph DB needed at this scale)
- **LLM + pages**: LLM gets text via tools; pages are rendered as MCP image responses visible to the *user*, not analyzed by the LLM
- **Sessions**: personal markdown files with YAML frontmatter — not in the DB

---

## Phase 1: Project Scaffolding

1. Create `pyproject.toml` with Python ≥3.11, dependencies: `mcp`, `pymupdf`, `surya-ocr`, `click` (CLI merge utility)
2. Create project layout under `src/ento_assist/`
3. Stub `server.py` — MCP server entry point with:
   - `instructions` string in the `initialize` response (see Phase 1a)
   - Empty tool groups registered for ingestion and identification modes

**Project structure:**
```
ento-assist/
  docs/core_plan.md
  src/ento_assist/
    __init__.py
    server.py
    db/
      schema.sql
      connection.py
    tools/
      ingestion.py
      identification.py
    extraction/
      pdf.py         # text + image extraction via pymupdf
      ocr.py         # surya fallback for image-only PDFs
      key_parser.py  # text → proposed couplet graph
    utils/
      merge.py       # CLI: merge two SQLite DBs
  pyproject.toml
  README.md
```

---

## Phase 1a: MCP Server Instructions (`server.py`)

The `initialize` response must include an `instructions` string covering:

**Mode selection:**
- Ingestion mode: user has a document to add to the database
- Identification mode: user wants to identify a specimen

**Ingestion workflow rules (enforced via instructions):**
- Always call `scan_document_structure` first on a newly registered document to establish a TOC before proposing any key structure
- Confirm key page boundaries with the user before calling `propose_key_structure`
- Never call `commit_extraction` without first calling `get_extraction_preview` and receiving explicit user approval
- If no figure references are found in a key, always ask the user whether inline figures are present before proceeding
- Process one key at a time; do not batch-commit multiple extractions without intermediate review

**Identification workflow rules:**
- Always use `advance_session` to move through a key — never infer the next couplet from text
- When couplet criteria are ambiguous or the user is unsure, call `look_ahead` before asking the user to decide
- Proactively call `lookup_term` for any morphological term that may be unfamiliar; do not assume the user knows it
- Proactively call `get_figure` for any figure referenced in a couplet leg
- At terminal taxon, always call `get_taxon_description` and present it to the user for verification

**Per-tool `description` fields** in each tool registration carry the tool-specific preconditions and expected next-call guidance (e.g., "`propose_key_structure` — call only after `scan_document_structure` has been reviewed and page boundaries confirmed with the user").

---

## Phase 2: SQLite Schema

All IDs are UUIDs (TEXT). This enables merge-by-ATTACH without integer PK collisions.

Tables:
- `documents` (id, title, path, source_type, created_at)
- `taxa` (id, name, rank, parent_id UUID nullable, description, doc_id, page_ref)
- `identification_keys` (id, doc_id, title, scope_taxon_id, start_couplet_id)
- `couplets` (id, key_id, number, page_ref)
- `couplet_legs` (id, couplet_id, leg_label [A/B], text, next_couplet_id nullable, terminal_taxon_id nullable)
- `figures` (id, doc_id, page_num, caption, image_data BLOB, bbox JSON)
- `couplet_leg_figures` (leg_id, figure_id, reference_text)
- `glossary_terms` (id, term, definition, doc_id, page_ref)

No session tables — identification sessions are personal, stored as local markdown files (see Phase 5).

SQLite FTS5 virtual table over `taxa.description` + `glossary_terms` for text search.

---

## Phase 3: Extraction Layer (`extraction/`)

**`pdf.py`** — all reads are transient (no page storage in DB)
- `register_document(path, title) → doc_id` — writes one row to `documents` table only
- `get_page_text(doc_id, page_num) → str` — reads live from PDF at stored path
- `render_page_image(doc_id, page_num) → bytes` — renders live from PDF; base64 PNG for MCP image response
- `crop_region(doc_id, page_num, bbox: {x0,y0,x1,y1}) → bytes` — live crop; result stored as BLOB in `figures` only when committed

**`ocr.py`**
- `run_ocr(doc_id, page_num) → str` — surya fallback for image-only pages; result returned transiently, not stored

**`key_parser.py`**
- `propose_key_structure(page_texts: list[str]) → ProposedKey` — pure text parsing, returns structured dict of proposed couplets with confidence scores. Called by LLM tool, not by LLM directly.

---

## Phase 4: Ingestion MCP Tools (`tools/ingestion.py`)

Tool group exposed to LLM agent during ingestion sessions:

| Tool | Purpose |
|------|---------|
| `ingest_document(path, title)` | Register document (one DB row); returns doc_id |
| `scan_document_structure(doc_id)` | Scans all page text to produce a TOC: detected keys, page ranges, section headings — for user confirmation before any extraction |
| `get_page_text(doc_id, page_num)` | LLM reads live text from PDF |
| `get_page_image(doc_id, page_num)` | MCP image response — user sees page, LLM doesn't analyze |
| `propose_key_structure(doc_id, page_start, page_end)` | Runs key_parser over live page texts; returns extraction_id + proposed couplets with page refs |
| `get_extraction_preview(extraction_id)` | Returns proposed couplets + page numbers for human spot-check |
| `correct_extraction(extraction_id, corrections: list)` | Apply human corrections before commit |
| `commit_extraction(extraction_id)` | Write validated couplet graph to DB |
| `crop_figure(doc_id, page_num, bbox, caption)` | Live crop → stores BLOB in `figures`; returns fig_id |
| `link_figure(fig_id, leg_id, ref_text)` | Associate figure with a couplet leg |
| `add_glossary_term(term, definition, doc_id, page_ref)` | Add to glossary |
| `add_taxon_description(taxon_id, text, doc_id, page_ref)` | Add description |

**Inline figure workflow** (no figure references in key):
1. LLM calls `get_page_text` → determines no figure refs present
2. LLM asks user: "Are there inline figures for this key?"
3. If yes: LLM calls `get_page_image` for each relevant page → user sees it
4. User describes region ("bottom-left figure on page 47 goes with couplet 3B")
5. LLM calls `crop_figure` with bbox, then `link_figure`

---

## Phase 5: Identification MCP Tools (`tools/identification.py`)

Session state is tracked in a local **markdown file** with YAML frontmatter (not in the DB). Format:

```
---
session_id: <uuid>
key_id: <uuid>
key_title: "Townes 1969 - Subfamily Key"
db_path: /path/to/ento.sqlite
started: 2026-05-31T14:23:00
last_updated: 2026-05-31T15:01:00
current_couplet_id: <uuid>
status: in_progress   # or: complete
terminal_taxon_id: null
---

# Identification Session: Ichneumonidae Subfamily Key

## Choices
1. **Couplet 1** (p. 12) → **A** — "Wings present..."

## Current Position
**Couplet 7** (p. 14)

## Notes
```

| Tool | Purpose |
|------|---------|
| `list_keys(taxon_name)` | List available keys scoped to a taxon |
| `start_session(key_id, output_path)` | Create session markdown file; return first couplet data |
| `resume_session(session_path)` | Load state from frontmatter; return current couplet data |
| `get_current_couplet(session_path)` | Couplet text, linked figures (as image responses), page ref |
| `advance_session(session_path, leg_label)` | Deterministic step; updates frontmatter + appends choice to body |
| `look_ahead(session_path, depth)` | Traverse both branches N levels; return terminal taxa candidates |
| `get_session_history(session_path)` | Read breadcrumb from file (no DB query) |
| `lookup_term(term)` | Glossary lookup with FTS fallback |
| `get_figure(fig_id)` | Figure BLOB + caption as MCP image response |
| `get_taxon_description(taxon_id)` | Full description text |
| `search_taxa(query)` | FTS5 search across taxa + descriptions |

---

## Phase 6: Merge Utility (`utils/merge.py`)

CLI: `python -m ento_assist.utils.merge db1.sqlite db2.sqlite --output merged.sqlite`

Strategy:
- ATTACH both source DBs
- INSERT OR IGNORE on UUID PKs (true duplicates are silently skipped)
- For content conflicts (same UUID, different content): log as warnings, keep source DB1 version by default, with `--prefer db2` flag

---

## Verification

1. Unit test `key_parser.py` against known key text samples (e.g., a simple 5-couplet key typed out manually)
2. Integration test: ingest a small public-domain PDF → verify page count, text extraction, image rendering
3. Integration test: commit a hand-crafted couplet graph → traverse it via identification tools → confirm terminal taxon reached
4. Test `look_ahead` returns correct candidate set for a known branching graph
5. Test merge utility: two DBs with overlapping + unique content → merged DB has union with no duplicates
6. Manual end-to-end: run against Claude Desktop, ingest one real key page, complete one identification session

---

## Further Considerations

1. **MCP image response support**: Claude Desktop renders MCP image responses inline. VS Code Copilot Chat may not — worth testing early. If not, a fallback (save image to temp path, return path instead) may be needed.
2. **key_parser complexity**: Fully automated couplet parsing from raw OCR text is hard (multi-column layout, couplet numbering styles vary by author). Start with a "best effort + always require human review" stance rather than trying to automate away the verification step.
3. **Figure bbox input UX**: Asking a user to provide pixel coordinates for `crop_figure` is awkward. A future enhancement would be a simple web UI for region selection — but out of scope for v1.
