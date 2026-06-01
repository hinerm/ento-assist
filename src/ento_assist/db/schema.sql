-- ento-assist database schema
-- All primary keys are UUIDs (TEXT) to support merge-by-ATTACH without collision.
-- Merge strategy: INSERT OR IGNORE on UUID PKs; content conflicts are logged externally.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Documents
-- Lightweight metadata only. The PDF file stays on disk at `path`.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,  -- UUID
    title       TEXT NOT NULL,
    path        TEXT NOT NULL,     -- absolute path to PDF on local filesystem
    source_type TEXT NOT NULL DEFAULT 'pdf',  -- 'pdf' | 'image_pdf'
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ---------------------------------------------------------------------------
-- Taxa
-- A taxon at any rank (order, family, subfamily, genus, species, etc.).
-- parent_id is nullable for top-level taxa.
-- description is the full prose description from the source document.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS taxa (
    id          TEXT PRIMARY KEY,  -- UUID
    name        TEXT NOT NULL,
    rank        TEXT NOT NULL,     -- e.g. 'family', 'subfamily', 'genus', 'species'
    parent_id   TEXT REFERENCES taxa(id),
    description TEXT,
    doc_id      TEXT REFERENCES documents(id),
    page_ref    INTEGER            -- page number in source document
);

CREATE INDEX IF NOT EXISTS idx_taxa_name ON taxa(name);
CREATE INDEX IF NOT EXISTS idx_taxa_parent ON taxa(parent_id);

-- ---------------------------------------------------------------------------
-- Identification keys
-- A single dichotomous key, scoped to identifying within a taxon.
-- base_taxon_id: the taxon the key operates on (e.g. Ichneumonidae at family rank).
-- leaf_taxon_rank: the rank the terminals resolve to (e.g. 'subfamily').
-- title is auto-generated as "<base_taxon_name> → <leaf_taxon_rank>".
-- start_couplet_id is set by build_finalize_key after all couplets are added.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS identification_keys (
    id               TEXT PRIMARY KEY,  -- UUID
    doc_id           TEXT NOT NULL REFERENCES documents(id),
    title            TEXT NOT NULL,
    base_taxon_id    TEXT NOT NULL REFERENCES taxa(id),
    leaf_taxon_rank  TEXT NOT NULL,     -- e.g. 'subfamily', 'genus', 'species'
    start_couplet_id TEXT               -- FK set by build_finalize_key; references couplets(id)
);

CREATE INDEX IF NOT EXISTS idx_keys_base_taxon ON identification_keys(base_taxon_id);

-- ---------------------------------------------------------------------------
-- Couplets
-- A single numbered couplet within a key. Always has exactly two legs (A and B).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS couplets (
    id       TEXT PRIMARY KEY,  -- UUID
    key_id   TEXT NOT NULL REFERENCES identification_keys(id),
    number   TEXT NOT NULL,     -- original couplet number/label from source (e.g. "1", "1a")
    page_ref INTEGER            -- page number in source document
);

CREATE INDEX IF NOT EXISTS idx_couplets_key ON couplets(key_id);

-- ---------------------------------------------------------------------------
-- Couplet legs
-- Each couplet has two legs (leg_label = 'A' or 'B').
-- next_couplet_number: the original source goto number (e.g. "4"); permanent audit trail.
-- next_couplet_id: resolved UUID FK, set by build_finalize_key. NULL until then.
-- Exactly one of next_couplet_id or terminal_taxon_id should be non-null
-- for a complete, validated key (both null = unresolved / needs review).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS couplet_legs (
    id                  TEXT PRIMARY KEY,  -- UUID
    couplet_id          TEXT NOT NULL REFERENCES couplets(id),
    leg_label           TEXT NOT NULL CHECK (leg_label IN ('A', 'B')),
    text                TEXT NOT NULL,
    next_couplet_number TEXT,              -- original source number; never cleared
    next_couplet_id     TEXT REFERENCES couplets(id),
    terminal_taxon_id   TEXT REFERENCES taxa(id)
);

CREATE INDEX IF NOT EXISTS idx_legs_couplet ON couplet_legs(couplet_id);

-- ---------------------------------------------------------------------------
-- Figures
-- Cropped image regions extracted from source documents.
-- image_data stores the PNG bytes as a BLOB (self-contained, portable).
-- bbox is a JSON object: {"x0": ..., "y0": ..., "x1": ..., "y1": ...}
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS figures (
    id         TEXT PRIMARY KEY,  -- UUID
    doc_id     TEXT NOT NULL REFERENCES documents(id),
    page_num   INTEGER NOT NULL,
    caption    TEXT,
    image_data BLOB NOT NULL,
    bbox       TEXT NOT NULL      -- JSON bbox from source page
);

CREATE INDEX IF NOT EXISTS idx_figures_doc ON figures(doc_id);

-- ---------------------------------------------------------------------------
-- Couplet leg ↔ figure associations
-- A leg may reference zero or more figures (labeled or inline).
-- reference_text is the original in-text reference (e.g. "Fig. 3b") or NULL
-- for inline/unlabeled figures identified interactively.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS couplet_leg_figures (
    leg_id         TEXT NOT NULL REFERENCES couplet_legs(id),
    figure_id      TEXT NOT NULL REFERENCES figures(id),
    reference_text TEXT,
    PRIMARY KEY (leg_id, figure_id)
);

-- ---------------------------------------------------------------------------
-- Glossary terms
-- Morphological and anatomical term definitions.
-- Global — not tied to any document.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS glossary_terms (
    id         TEXT PRIMARY KEY,  -- UUID
    term       TEXT NOT NULL,
    definition TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_glossary_term ON glossary_terms(term);

-- ---------------------------------------------------------------------------
-- Full-text search (FTS5)
-- Covers taxon descriptions and glossary definitions for term/description lookup.
-- ---------------------------------------------------------------------------
CREATE VIRTUAL TABLE IF NOT EXISTS fts_content USING fts5(
    content_id,        -- UUID of the source row
    content_type,      -- 'taxon' | 'glossary'
    label,             -- taxon name or glossary term
    body,              -- description or definition text
    tokenize = "unicode61"
);
