# Test Plan — Post-Refactor

Tests are currently disabled while the ingestion workflow is being iterated on.
This document captures what needs to be covered when tests are re-enabled.

---

## Schema (`tests/test_schema.py`)

- `identification_keys` has `base_taxon_id TEXT NOT NULL` column
- `identification_keys` has `leaf_taxon_rank TEXT NOT NULL` column
- `identification_keys` no longer has `scope_taxon_id` column
- `couplet_legs` has `next_couplet_number TEXT` column (nullable)
- `glossary_terms` has no `doc_id` column
- `glossary_terms` has no `page_ref` column
- UNIQUE index on `glossary_terms(term)` still present
- FK from `identification_keys.base_taxon_id` → `taxa(id)` enforced

---

## Scan heuristics (`tests/test_key_parser.py`)

Replace all tests that reference `parse_key_from_texts`, `ProposedKey`,
`ProposedCouplet`, `ProposedLeg` — those are deleted.

New tests for `scan_for_key_boundaries`:
- Page with back-reference pattern `5(4)` is treated as key content
- Page with dotted leader `..........3` is treated as key content
- Page with neither signal is not treated as key content
- `page_end` is set to the last page with key content, not the last page overall
  (verify the 2-page overshoot is gone)
- Two separate key headers in one document produce two separate `KeyRegion` objects
- `_has_key_content` helper unit tests (both patterns, combined, neither)

---

## `util_pick_file` (`tests/test_system.py`)

- On macOS (`sys.platform == "darwin"`), `osascript` subprocess is called
  (mock `subprocess.run` to return a known path)
- Cancellation (empty stdout from osascript) raises `ValueError`
- On non-macOS, tkinter path is taken (mock `tkinter.filedialog.askopenfilename`)

---

## New ingestion workflow (`tests/test_ingestion_workflow.py`)

Remove all tests that reference:
- `build_propose_key`
- `build_submit_key`
- `build_preview_extraction`
- `build_correct_extraction`
- `build_commit_extraction`
- `_pending_extractions`

### `build_create_key`
- Happy path: creates key row; returns key_id and correct auto-generated title
- Creates base taxon if it does not exist
- Reuses existing base taxon if name+rank already present
- `leaf_taxon_rank` is stored correctly

### `build_add_couplet`
- Happy path: inserts couplet + both legs; returns correct IDs
- `next_couplet_number` stored as text on legs with goto; `next_couplet_id` NULL
- Terminal taxon: creates stub taxon if not present; stores `terminal_taxon_id`
- Reuses existing taxon for terminal if name already present
- Raises `ValueError` if `key_id` does not exist
- Raises `ValueError` if couplet number already exists in key
- Raises `ValueError` if both `goto` and `terminal` are provided for same leg
- Warns (does not raise) if a leg has neither goto nor terminal

### `build_finalize_key`
- Happy path: all `next_couplet_number` values resolve to `next_couplet_id`
- `start_couplet_id` is set to the couplet numbered "1"
- `start_couplet_id` falls back to lowest-numbered couplet when "1" absent
- Unresolved goto (number not found) reported in `unresolved` list, not raised
- `next_couplet_number` values are NOT cleared after finalization (audit trail)
- Raises `ValueError` if `key_id` does not exist

### Full round-trip
- `build_create_key` → `build_add_couplet` × 3 → `build_finalize_key`
- Verify traversal: couplet 1 leg B goto "2" resolves to couplet 2's UUID

### `build_add_term`
- Inserts term with no doc_id or page_ref
- Silent no-op on duplicate term (UNIQUE constraint)
- Term stored lowercase/stripped
- FTS content row inserted

---

## Identification workflow (`tests/test_identification_workflow.py`)

- `run_list_keys`: update expected dict shape (no `scope_taxon`, instead
  expect `base_taxon` and `leaf_taxon_rank` fields from joined query)
- All other identification tests should pass unchanged assuming `run_start_session`
  still works from `start_couplet_id`

---

## Merge utility (`tests/test_merge.py`)

- Verify merge handles new `base_taxon_id` / `leaf_taxon_rank` columns on
  `identification_keys` correctly (INSERT OR IGNORE on UUID PKs still applies)
- Verify merge handles absence of `doc_id`/`page_ref` on `glossary_terms`
