# ento-assist Test Plan

## Overview

The test suite is organized into four phases based on external dependencies. Phases 1,
2, and 4 require no external fixtures and can be run immediately. Phase 3 is blocked
until a sample dichotomous key PDF is provided (see [resources needed](#resources-needed-from-you)).

**Framework**: pytest + pytest-cov (already in dev dependencies)
**Target coverage**: 70%+ overall
**OCR tests**: Excluded entirely — surya-ocr is best validated manually

---

## Resources Needed From You

**`tests/fixtures/sample_key.pdf`** — a real dichotomous key PDF with:
- Text-based pages (not fully image-scanned) — at least 3 pages
- One key with 3–7 couplets and at least 2 identifiable terminal taxa (genus + species)

To seed exact assertions after the first run, also provide:
- Total page count of the PDF
- Which page number contains the key (1-indexed)
- 1–2 terminal taxon names visible in that key
- Whether any pages are image-only (relevant for `get_page_text` boundary test)

See `tests/fixtures/README.md` for details on where to place the file.

---

## File Layout

```
tests/
├── __init__.py
├── conftest.py                          # Shared pytest fixtures
├── test_key_parser.py                   # Phase 2 — pure logic unit tests
├── test_db_connection.py                # Phase 2 — DB connection + context manager
├── test_schema.py                       # Phase 2 — schema structure validation
├── test_merge.py                        # Phase 2 — database merge utility
├── test_server.py                       # Phase 2 — server bootstrap
├── test_pdf.py                          # Phase 3 — PDF I/O (needs fixture PDF)
├── test_ingestion_workflow.py           # Phase 3 — ingestion pipeline E2E
└── test_identification_workflow.py      # Phase 4 — identification session E2E
fixtures/
└── README.md                            # Instructions for placing sample_key.pdf
```

---

## Phase 1: Scaffolding

**File**: `tests/conftest.py`

Shared fixtures available to all test modules:

| Fixture | Scope | Description |
|---------|-------|-------------|
| `temp_db(tmp_path)` | function | Fresh SQLite DB with full schema applied; path returned |
| `sample_pdf_path` | session | Resolves `tests/fixtures/sample_key.pdf`; calls `pytest.skip()` if absent |
| `minimal_couplet_graph(temp_db)` | function | Programmatically inserts a 5-couplet binary tree (depth 3) with 2 terminal taxa directly into the DB; returns `(db_path, key_id)` |
| `session_file(tmp_path, minimal_couplet_graph)` | function | Writes a starter markdown session file pointing at the first couplet; returns path |

The `minimal_couplet_graph` fixture uses taxa `"Aedes aegypti"` and `"Culex pipiens"` as
leaves and constructs the couplet structure entirely with raw `INSERT` statements — no
tool functions involved. This avoids circular dependencies between test modules.

---

## Phase 2: Unit Tests (no external fixtures needed)

### `tests/test_key_parser.py` (~18 tests)

Tests the heuristic couplet parser in `extraction/key_parser.py`. All tests use
in-process string fixtures — no files or DB required.

| Test | What it checks |
|------|----------------|
| `test_parse_couplet_numbers_dot` | `"1."` style numbering detected |
| `test_parse_couplet_numbers_alpha` | `"1a."` style numbering detected |
| `test_parse_goto_numeric` | `"go to 5"` target extracted as int |
| `test_parse_goto_trailing_dot` | `"... 7"` trailing-dot form extracted |
| `test_parse_terminal_taxon` | Genus + species binomial detected |
| `test_parse_figure_ref_single` | `"Fig. 3b"` ref extracted |
| `test_parse_figure_ref_range` | `"Figs. 3–5"` range extracted |
| `test_full_parse_clean_key` | 4-couplet hand-crafted text → correct couplet count, leg texts, goto targets |
| `test_full_parse_terminal_leaves` | Terminal taxa appear as leaf nodes, no goto |
| `test_confidence_clean` | Well-formed key → confidence == 1.0 |
| `test_confidence_incomplete` | One unpaired leg → confidence ~0.5 |
| `test_confidence_low` | Many missing/malformed couplets → confidence ≤ 0.5 |
| `test_warnings_missing_pair` | Unpaired couplet generates a warning |
| `test_warnings_no_numbers` | Unnumbered block generates a warning |
| `test_proposed_key_fields` | `ProposedKey` has `keys`, `title`, `confidence`, `warnings` |
| `test_empty_input` | Empty string → empty key list, no crash |
| `test_single_leg_only` | Block with only one lead → warning, not crash |
| `test_malformed_numbering` | Skipped numbers (1, 3, 5…) → warnings generated |

### `tests/test_db_connection.py` (~9 tests)

Tests `db/connection.py`. Each test gets a fresh temp path from pytest `tmp_path`.

| Test | What it checks |
|------|----------------|
| `test_schema_created_on_first_open` | All 9 expected tables exist after first open |
| `test_schema_idempotent` | Opening same path twice → no error, same tables |
| `test_wal_mode_enabled` | `PRAGMA journal_mode` returns `"wal"` |
| `test_foreign_keys_enforced` | `PRAGMA foreign_keys` returns `1` |
| `test_row_factory` | Row columns accessible by name |
| `test_context_manager_commit` | Write inside `with` block → persists after close |
| `test_context_manager_rollback` | Exception inside `with` block → row not written |
| `test_no_partial_write_on_rollback` | After rollback, table count unchanged |
| `test_multiple_connections_same_file` | Two sequential opens → consistent state |

### `tests/test_schema.py` (~7 tests)

Validates the static structure of `db/schema.sql` by opening a fresh DB and querying
`PRAGMA` metadata.

| Test | What it checks |
|------|----------------|
| `test_all_tables_exist` | All 9 tables present |
| `test_leg_label_check_constraint` | `'A'` and `'B'` accepted; `'C'` raises `IntegrityError` |
| `test_glossary_term_unique` | Inserting duplicate term raises `IntegrityError` |
| `test_fts_content_queryable` | `SELECT … MATCH 'test'` on `fts_content` does not raise |
| `test_key_indexes_exist` | Performance indexes present on `couplet_legs`, `figures`, `taxa` |
| `test_uuid_primary_keys` | Primary key columns are TEXT (UUID-compatible) |
| `test_couplet_page_columns` | `couplets` table has `page_num` column |

### `tests/test_merge.py` (~9 tests)

Tests `utils/merge.py`. All DBs are created programmatically — no fixture files.

| Test | What it checks |
|------|----------------|
| `test_empty_merge` | Schema-only + schema-only → all tables, 0 rows |
| `test_disjoint_uuids` | Rows with different UUIDs in A and B → both present after merge |
| `test_exact_duplicate_skipped` | Same UUID + identical data in both → 1 row after merge |
| `test_content_conflict_detected` | Same UUID, different column value → logged/handled without crash |
| `test_blob_conflict_detected` | Same UUID, different `figures.image_data` → handled without crash |
| `test_fk_integrity_post_merge` | All foreign keys valid in merged DB |
| `test_fts_sync_after_merge` | Glossary term inserted in source → queryable in merged DB |
| `test_cli_exit_code_success` | `main(['--source', src, '--dest', dst])` → exit code 0 |
| `test_cli_missing_arg` | Missing `--dest` → non-zero exit or `SystemExit` |

### `tests/test_server.py` (~4 tests)

Tests `server.py` bootstrap logic.

| Test | What it checks |
|------|----------------|
| `test_build_server_name` | `build_server()` returns FastMCP with name `"ento-assist"` |
| `test_server_instructions_nonempty` | `SERVER_INSTRUCTIONS` is non-empty string |
| `test_main_missing_env_exits` | `main()` with `ENTO_DB_PATH` unset → `SystemExit` or exit code 1 |
| `test_main_valid_env_no_raise` | `main()` with valid `ENTO_DB_PATH` + mocked `mcp.run()` → no error |

---

## Phase 3: Integration Tests (blocked on sample PDF)

Phase 3 tests are present in the repo but unconditionally skipped via the
`sample_pdf_path` fixture until `tests/fixtures/sample_key.pdf` is provided.

### `tests/test_pdf.py` (~7 tests)

| Test | What it checks |
|------|----------------|
| `test_register_document_returns_uuid` | `register_document()` returns valid UUID, row in `documents` |
| `test_get_page_count` | Count matches expected for fixture PDF |
| `test_get_page_text_text_page` | Text page → non-empty string |
| `test_get_page_text_image_page` | Image-only page → very short / empty string |
| `test_render_page_image_is_png` | Bytes start with PNG magic `\x89PNG` |
| `test_render_page_image_dpi_scaling` | 2× DPI → larger byte count than 1× DPI |
| `test_crop_region_valid_bbox` | `crop_region()` with valid bbox → valid PNG bytes |

### `tests/test_ingestion_workflow.py` (~10 tests)

Full pipeline integration using the fixture PDF.

Initial assertions are "shape" tests (ranges and structure) and should be upgraded to
exact values after the first confirmed run.

| Test | What it checks |
|------|----------------|
| `test_ingest_and_register` | `ingest_document()` → doc_id exists in `documents` |
| `test_scan_structure` | `scan_document_structure()` on key page range → non-empty result |
| `test_propose_key_structure` | `propose_key_structure()` → extraction_id, 3–20 proposed couplets |
| `test_get_extraction_preview` | Preview string contains couplet markers |
| `test_correct_lead_text` | `correct_extraction()` on a lead → preview reflects change |
| `test_commit_writes_to_db` | `commit_extraction()` → couplet + leg rows in DB |
| `test_commit_is_idempotent` | Re-commit same extraction → no duplicate rows or clear error |
| `test_link_figure` | `crop_figure()` + `link_figure()` → `figure_couplet_links` row exists |
| `test_add_glossary_term` | `add_glossary_term()` → term queryable via FTS5 |
| `test_add_taxon_description` | `add_taxon_description()` → row in `taxa` |

---

## Phase 4: Identification Workflow

Uses `minimal_couplet_graph` and `session_file` fixtures from `conftest.py` — no PDF needed.

### `tests/test_identification_workflow.py` (~13 tests)

| Test | What it checks |
|------|----------------|
| `test_start_session_creates_file` | `start_session()` → markdown file created with YAML frontmatter |
| `test_start_session_idempotent` | Re-starting existing session → error or returns existing state |
| `test_resume_session` | `resume_session()` on existing file → reads current couplet position |
| `test_advance_session_a` | `advance_session('A')` → moves to A-branch couplet, file updated |
| `test_advance_session_b` | `advance_session('B')` → moves to B-branch couplet |
| `test_traversal_reaches_terminal` | Following correct A/B path → correct terminal taxon name returned |
| `test_invalid_choice_rejected` | `advance_session('C')` → error, position unchanged |
| `test_look_ahead_lists_branches` | `look_ahead()` from mid-key → both branch paths listed with terminal taxa |
| `test_collect_terminals_cycle_safety` | `_collect_terminals()` on cyclic graph (forced via DB) → no infinite loop |
| `test_lookup_term_exact` | `lookup_term()` for exact glossary term → correct definition |
| `test_lookup_term_fts_fallback` | `lookup_term()` with near-match → FTS result returned |
| `test_search_taxa` | `search_taxa("Aedes")` → returns row with `"Aedes aegypti"` |
| `test_list_keys_no_filter` | `list_keys()` → all keys returned |
| `test_list_keys_taxon_filter` | `list_keys(taxon="Aedes aegypti")` → only relevant keys |

---

## Running the Tests

```bash
# Run all tests (Phase 3 auto-skipped without fixture PDF)
uv run pytest

# Run with coverage report
uv run pytest --cov=ento_assist --cov-report=term-missing

# Run a specific phase
uv run pytest tests/test_key_parser.py tests/test_db_connection.py -v

# After providing tests/fixtures/sample_key.pdf:
uv run pytest tests/test_pdf.py tests/test_ingestion_workflow.py -v
```

---

## Key Design Decisions

- **No MCP transport testing**: tool functions are called directly as regular Python. If
  they take a FastMCP `ctx` parameter, it is mocked with `unittest.mock.MagicMock`.
- **OCR excluded**: surya-ocr is GPU-heavy and best validated manually.
- **PDF tests gracefully skip**: `sample_pdf_path` fixture calls `pytest.skip()` when
  the fixture file is absent, so `pytest` always exits cleanly.
- **No ordering dependency**: each test is fully independent; `temp_db` and `session_file`
  fixtures use pytest `tmp_path` for isolation.
- **Shape-first assertions for ingestion**: exact couplet counts / lead text strings are
  added after a first confirmed run to avoid brittleness.
