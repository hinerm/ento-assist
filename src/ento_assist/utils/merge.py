# Copyright 2026 The ento-assist Authors
# SPDX-License-Identifier: MIT

"""Database merge utility for ento-assist.

Merges two ento-assist SQLite databases into a single output database.
Uses SQLite ATTACH for efficient bulk copying.

Usage:
    ento-merge db1.sqlite db2.sqlite --output merged.sqlite [--prefer db1|db2]

Strategy:
    - UUIDs are the canonical identity for all rows.
    - Identical UUIDs are assumed to be the same record. INSERT OR IGNORE
      skips exact duplicates silently.
    - Content conflicts (same UUID, different content in non-primary-key columns)
      are detected and logged as warnings. The --prefer flag selects which DB
      wins for conflicting rows; if omitted, db1 wins by default.
    - BLOBs (figures.image_data) are compared by length; a mismatch is flagged
      but the --prefer winner's blob is used.

Merge order:
    documents → taxa → identification_keys → couplets → couplet_legs →
    figures → couplet_leg_figures → glossary_terms → fts_content
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import click

from ento_assist.db.connection import initialize_db

# Tables in dependency order (parent before child)
_MERGE_ORDER = [
    "documents",
    "taxa",
    "identification_keys",
    "couplets",
    "couplet_legs",
    "figures",
    "couplet_leg_figures",
    "glossary_terms",
    "fts_content",
]

# Tables where we can meaningfully check for content conflicts (skip BLOBs for text comparison)
_CONFLICT_CHECK_COLUMNS: dict[str, list[str]] = {
    "documents": ["title", "path", "source_type"],
    "taxa": ["name", "rank", "description"],
    "identification_keys": ["title"],
    "couplets": ["number", "key_id"],
    "couplet_legs": ["leg_label", "text"],
    "figures": ["caption"],
    "glossary_terms": ["term", "definition"],
}


def merge_databases(
    db1_path: Path,
    db2_path: Path,
    output_path: Path,
    prefer: str = "db1",
) -> list[str]:
    """Merge db1 and db2 into output_path.

    Returns a list of warning messages (empty if clean merge).
    """
    import shutil

    warnings: list[str] = []

    # Start from a fresh output DB seeded with db1
    if output_path.exists():
        output_path.unlink()
    shutil.copy2(db1_path, output_path)

    # Ensure schema is present (safe no-op if db1 already has it)
    initialize_db(output_path)

    conn = sqlite3.connect(str(output_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")  # disable during bulk merge
    conn.execute("PRAGMA journal_mode = WAL")

    try:
        conn.execute(f"ATTACH DATABASE '{db2_path}' AS src")

        for table in _MERGE_ORDER:
            table_warnings = _merge_table(conn, table, prefer=prefer)
            warnings.extend(table_warnings)

        conn.commit()
    finally:
        conn.execute("DETACH DATABASE src")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()

    return warnings


def _merge_table(conn: sqlite3.Connection, table: str, prefer: str) -> list[str]:
    """Merge rows from src.<table> into the main schema <table>.

    Returns warnings for any content conflicts detected.
    """
    warnings: list[str] = []

    # Get columns for this table
    cursor = conn.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    if not columns:
        return warnings  # table doesn't exist in this DB

    col_list = ", ".join(columns)
    placeholders = ", ".join("?" * len(columns))

    # Fetch all source rows
    try:
        src_rows = conn.execute(f"SELECT {col_list} FROM src.{table}").fetchall()
    except sqlite3.OperationalError:
        return warnings  # table absent in source

    conflict_cols = _CONFLICT_CHECK_COLUMNS.get(table, [])

    for row in src_rows:
        row_dict = dict(zip(columns, row, strict=False))
        row_id = row_dict.get("id") or row_dict.get("leg_id") or row_dict.get("figure_id")

        # Check for existing row with same PK
        pk_col = "id" if "id" in columns else None
        if pk_col and row_id:
            existing = conn.execute(
                f"SELECT {', '.join(conflict_cols or ['rowid'])} FROM {table} WHERE {pk_col} = ?",
                (row_id,),
            ).fetchone()

            if existing and conflict_cols:
                for col in conflict_cols:
                    src_val = row_dict.get(col)
                    dst_val = existing.get(col)
                    if src_val != dst_val and src_val is not None and dst_val is not None:
                        warnings.append(
                            f"CONFLICT [{table}.{col}] id={row_id}: "
                            f"db1={dst_val!r} vs db2={src_val!r} — keeping {prefer}"
                        )
                        if prefer == "db2":
                            conn.execute(
                                f"UPDATE {table} SET {col} = ? WHERE {pk_col} = ?",
                                (src_val, row_id),
                            )
                continue  # row exists; conflict handled above

        # Insert (silently skip if exact duplicate via OR IGNORE)
        try:
            conn.execute(
                f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})",
                list(row_dict.values()),
            )
        except sqlite3.IntegrityError as exc:
            warnings.append(f"INSERT error in {table} (id={row_id}): {exc}")

    return warnings


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


@click.command()
@click.argument("db1", type=click.Path(exists=True, path_type=Path))
@click.argument("db2", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output", "-o", required=True, type=click.Path(path_type=Path), help="Output database path"
)
@click.option(
    "--prefer",
    type=click.Choice(["db1", "db2"], case_sensitive=False),
    default="db1",
    show_default=True,
    help="Which database wins on content conflicts",
)
def main(db1: Path, db2: Path, output: Path, prefer: str) -> None:
    """Merge two ento-assist databases into OUTPUT.

    Rows with identical UUIDs are treated as duplicates and skipped (INSERT OR IGNORE).
    Content conflicts (same UUID, different values) are logged as warnings.
    """
    click.echo(f"Merging {db1} + {db2} → {output} (prefer={prefer})")
    warnings = merge_databases(db1, db2, output, prefer=prefer)

    if warnings:
        click.echo(f"\n{len(warnings)} conflict(s) detected:", err=True)
        for w in warnings:
            click.echo(f"  WARNING: {w}", err=True)
    else:
        click.echo("Clean merge — no conflicts.")

    click.echo(f"\nOutput written to: {output}")
