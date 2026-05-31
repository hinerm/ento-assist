# ento-assist

An LLM-assisted entomological specimen identification system, delivered as a
[Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server.

The server exposes a structured SQLite knowledge base built from printed PDF
identification keys (dichotomous keys) to any MCP-capable agent. The agent
guides a user who has a physical specimen in hand through couplet-by-couplet
identification, looking up morphological terms, displaying key figures, and
confirming terminal taxon descriptions — always with the human making every
identification decision.

Designed for ichneumonoid wasps (Ichneumonoidea), but the data model is
general-purpose and works for any group covered by a dichotomous key.

---

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the server](#running-the-server)
- [Connecting an MCP client](#connecting-an-mcp-client)
- [Workflow overview](#workflow-overview)
  - [Ingestion](#ingestion-mode-building-the-knowledge-base)
  - [Identification](#identification-mode-identifying-a-specimen)
- [Merging databases](#merging-databases)
- [Architecture](#architecture)
- [Database conventions](#database-conventions)
- [Development](#development)

---

## Features

- **Structured key ingestion** — heuristic PDF-to-couplet-graph parser with
  confidence scoring; human review required before any data is committed.
- **Deterministic key traversal** — adjacency-list graph ensures the agent
  never invents or skips a couplet step.
- **Figure support** — inline key illustrations stored as PNG BLOBs; displayed
  to the user at the relevant couplet.
- **OCR fallback** — surya-ocr for scanned / image-only PDFs.
- **Glossary & FTS5 search** — morphological terms and taxon descriptions are
  full-text searchable.
- **Portable single-file database** — SQLite with UUID primary keys; easily
  shared with colleagues.
- **Database merge** — combine independently-built databases with
  conflict detection.
- **Personal session files** — identification sessions live as local markdown
  files with YAML frontmatter; never stored in the shared DB.

---

## Prerequisites

- **Python ≥ 3.11**
- **[uv](https://docs.astral.sh/uv/)** package manager
- A PDF of a dichotomous key, or an existing `.sqlite` database
- Any MCP-capable agent (see [Connecting an MCP client](#connecting-an-mcp-client))

For OCR on scanned PDFs:
- A CUDA-capable GPU is recommended (surya-ocr falls back to CPU but is slow).

---

## Installation

```bash
git clone https://github.com/hinerm/ento-assist.git
cd ento-assist
uv sync
```

For development (includes ruff, mypy, pytest, pre-commit):

```bash
uv sync --group dev
uv run pre-commit install
```

---

## Configuration

The server requires one environment variable:

| Variable | Description |
|---|---|
| `ENTO_DB_PATH` | Absolute path to your SQLite database file. Created on first run if it does not exist. |

For VS Code, this is preconfigured in `.vscode/mcp.json`. For other clients
(Claude Desktop, etc.) or direct server invocations, set it in the environment
or client config:

```bash
export ENTO_DB_PATH=~/.entoassist/db.sqlite
```

---

## Running the server

```bash
export ENTO_DB_PATH=~/.entoassist/db.sqlite
uv run ento-assist
```

The server runs over **stdio** (standard MCP transport). It is not a web
server; launch it via an MCP client (VS Code, Claude Desktop, etc.).

---

## Connecting an MCP client

ento-assist works with any client that supports the MCP stdio transport.

### VS Code (GitHub Copilot)

The repository includes `.vscode/mcp.json` which registers the server
automatically. It is preconfigured to use `~/.entoassist/db.sqlite` as the
database path (created on first run if it does not exist). To use a different
location, edit the `ENTO_DB_PATH` value in `.vscode/mcp.json` directly.

The server will appear in the MCP servers list and start on demand.

### Claude Desktop

Add an entry to your `claude_desktop_config.json`, with `ENTO_DB_PATH` edited as needed:

```json
{
  "mcpServers": {
    "ento-assist": {
      "command": "uv",
      "args": ["--directory", "/path/to/ento-assist", "run", "ento-assist"],
      "env": { "ENTO_DB_PATH": "/Users/you/.entoassist/db.sqlite" }
    }
  }
}
```

### Other clients

Any client that launches a stdio MCP server can use the same pattern:
command `uv`, args `["--directory", "<repo>", "run", "ento-assist"]`,
with `ENTO_DB_PATH` set in the environment.

---

## Workflow overview

### Ingestion mode — building the knowledge base

The ingestion workflow is always human-supervised. The agent will never commit
data without your explicit approval.

1. **Register** a PDF document:
   > "Register this PDF: `/books/Townes1969.pdf`, title `Townes 1969 Ichneumonidae vol 1`"

2. **Scan** the document structure — the agent calls `scan_document_structure`
   and shows you detected key regions with page ranges for confirmation.

3. **Propose** a key structure for a confirmed page range — the agent calls
   `propose_key_structure` and then immediately shows you the full couplet
   preview, highlighting any couplets with confidence < 0.7.

4. **Correct** any parsing errors — describe what needs fixing and the agent
   applies changes via `correct_extraction`.

5. **Approve & commit** — the agent asks for explicit confirmation before
   calling `commit_extraction`. Answer *yes* to write to the database.

6. **Figures** (optional) — ask the agent to display a page image; describe
   the bounding box of a figure and the agent stores it and links it to the
   appropriate couplet leg.

7. **Glossary / taxon descriptions** (optional) — ask the agent to add
   morphological term definitions or full taxon descriptions.

### Identification mode — identifying a specimen

1. **Choose a key:**
   > "List available keys for Ichneumonidae"

2. **Start a session** — the agent creates a local markdown session file and
   presents the first couplet.

3. **Work through each couplet** — the agent presents both legs (A and B),
   automatically looks up any technical terms, and displays linked figures.
   You answer **A** or **B** for each couplet.

4. **Look-ahead** (optional) — if uncertain, ask: "What taxa would I reach
   via each branch?" The agent traverses ahead up to 3 levels.

5. **Terminal taxon** — when you reach a terminal, the agent fetches the
   full taxon description and asks you to confirm the match before concluding.

Session files are saved as plain markdown with YAML frontmatter. They are
yours to keep, share, or annotate freely — they are never stored in the
shared database.

---

## Merging databases

Two databases built independently can be merged:

```bash
ento-merge db1.sqlite db2.sqlite --output merged.sqlite [--prefer db1|db2]
```

- Rows with identical UUIDs are treated as exact duplicates and skipped
  (`INSERT OR IGNORE`).
- Content conflicts (same UUID, different values) are detected and logged
  as warnings. The `--prefer` flag controls which database wins; `db1` is
  the default.

---

## Architecture

```
src/ento_assist/
  server.py            — FastMCP server entry point; SERVER_INSTRUCTIONS string
  db/
    schema.sql         — SQLite schema (source of truth for all tables)
    connection.py      — Context-manager connection with FK enforcement + WAL
  extraction/
    pdf.py             — Transient PDF operations (pymupdf); nothing persisted
    ocr.py             — surya-ocr fallback for image-only pages (lazy import)
    key_parser.py      — Heuristic couplet parser → ProposedKey with confidence scores
  tools/
    ingestion.py       — MCP tools: register → scan → propose → review → commit
    identification.py  — MCP tools: session file + deterministic key traversal
  utils/
    merge.py           — ento-merge CLI (SQLite ATTACH + INSERT OR IGNORE)
tests/                 — pytest test suite
docs/
  core_plan.md         — Full architectural design document
```

---

## Database conventions

| Convention | Reason |
|---|---|
| All primary keys are `UUID TEXT` | Enables conflict-free merge via ATTACH |
| PDFs stay on disk; only path stored | Lightweight DB; PDFs can be large |
| Page numbers 0-indexed in DB | 1-indexed in all tool inputs/outputs |
| Figures stored as PNG BLOBs | Single-file portability when sharing |
| FTS5 virtual table `fts_content` | Full-text search over taxa + glossary |
| No session tables in DB | Sessions are personal, not shared artifacts |

---

## Development

### First-time setup

After cloning, install dependencies and wire up the git hooks:

```bash
uv sync --group dev
uv run pre-commit install
```

### Pre-commit hooks

Hooks run automatically on `git commit` once installed, to verify core repository health:
1. General hygiene (trailing whitespace, YAML/TOML validity, large-file guard)
2. `ruff` lint + auto-fix, then `ruff-format`
3. `insert-license` to verify `.py` files have up-to-date license headers
4. `mypy` type checking on `src/`

If a commit is rejected, many issues (formatting, import order, license headers)
will have been auto-fixed in place. Simply stage the modified files and retry
the commit.

To run all hooks manually against every file (useful after cloning or rebasing):

```bash
uv run pre-commit run --all-files
```

### Running checks manually

```bash
# Format + lint
uv run ruff format src/ tests/
uv run ruff check --fix src/ tests/

# Type checking
uv run mypy src/

# Tests with coverage
uv run pytest
```

### Notes for contributors

- `surya-ocr` imports are **lazy** (inside functions) to avoid mandatory GPU
  initialisation at import time. Keep them that way.
- `_pending_extractions` in `ingestion.py` is process-scoped in-memory state.
  It does not survive server restarts; re-run `propose_key_structure` if needed.
- The key parser is deliberately conservative. Confidence < 0.7 means the
  couplet needs manual review before committing — do not raise this threshold
  without a corresponding improvement to the parser heuristics.
- The `SERVER_INSTRUCTIONS` string in `server.py` encodes mandatory
  human-in-the-loop workflow rules sent to the agent on every session start
  via the MCP `initialize` response. Do not weaken these rules.

---

## License

MIT — see [LICENSE](LICENSE).
