# Copyright 2026 The ento-assist Authors
# SPDX-License-Identifier: MIT

"""Dichotomous key boundary scanner for ento-assist.

Scans raw page text to detect likely key regions within a document.
Results are presented to the user for confirmation before any couplets
are committed.  Couplet interpretation is performed interactively by the
LLM agent, one couplet at a time, using build_add_couplet.

Public API:
    scan_for_key_boundaries(page_texts, page_start) -> list[KeyRegion]
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class KeyRegion:
    """A detected key region within a document, before parsing."""

    title: str
    page_start: int  # 0-indexed
    page_end: int  # 0-indexed, inclusive
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Patterns that commonly indicate the start of a dichotomous key
_KEY_HEADER_PATTERNS = [
    re.compile(r"\bkey\s+to\b", re.IGNORECASE),
    re.compile(r"\bdetermination\s+key\b", re.IGNORECASE),
    re.compile(r"\bidentification\s+key\b", re.IGNORECASE),
    re.compile(r"\bcouplet\s+1\b", re.IGNORECASE),
]

# Positive signals that a page contains dichotomous key couplet content.
# (1) Couplet back-reference: a number followed by a parenthetical previous number,
#     e.g. "5(4)" or "12(11)" — highly specific to dichotomous key format.
_BACKREF_PATTERN = re.compile(r"\b\d+\(\d+\)")

# (2) Dotted leader ending in a standalone number — the classic "goto" indicator,
#     e.g. "....................3" or ". . . . . 12"
_DOTTED_GOTO_PATTERN = re.compile(r"[.]{4,}\s*\d+\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def scan_for_key_boundaries(
    page_texts: list[str],
    page_start: int = 0,
) -> list[KeyRegion]:
    """Scan page texts to detect likely key regions.

    Key start is detected via header phrases ("key to", etc.).
    Key end is set to the last page in the run that still contains
    positive key-content signals (back-references or dotted leaders).
    This avoids document-specific section-name heuristics.

    Args:
        page_texts: List of text strings, one per page, in order.
        page_start: The 0-indexed page number of the first entry in page_texts.

    Returns:
        List of detected KeyRegion objects, sorted by page_start.
    """
    regions: list[KeyRegion] = []
    in_key = False
    key_start_page = 0
    key_title = ""
    last_key_content_page = 0

    for i, text in enumerate(page_texts):
        abs_page = page_start + i

        # Detect key header
        header_match = None
        for pat in _KEY_HEADER_PATTERNS:
            m = pat.search(text)
            if m:
                header_match = m
                break

        if header_match and not in_key:
            in_key = True
            key_start_page = abs_page
            last_key_content_page = abs_page
            # Extract surrounding line as title candidate
            line_start = text.rfind("\n", 0, header_match.start())
            line_end = text.find("\n", header_match.end())
            title_line = text[line_start + 1 : line_end if line_end != -1 else None]
            key_title = title_line.strip()[:120]
        elif in_key:
            if _has_key_content(text):
                last_key_content_page = abs_page
            elif header_match:
                # A new key starts — close the current one first
                regions.append(
                    KeyRegion(
                        title=key_title or f"Key on page {key_start_page + 1}",
                        page_start=key_start_page,
                        page_end=last_key_content_page,
                    )
                )
                in_key = True
                key_start_page = abs_page
                last_key_content_page = abs_page
                line_start = text.rfind("\n", 0, header_match.start())
                line_end = text.find("\n", header_match.end())
                title_line = text[line_start + 1 : line_end if line_end != -1 else None]
                key_title = title_line.strip()[:120]

    # Close any open region
    if in_key:
        regions.append(
            KeyRegion(
                title=key_title or f"Key on page {key_start_page + 1}",
                page_start=key_start_page,
                page_end=last_key_content_page,
            )
        )

    return regions


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _has_key_content(text: str) -> bool:
    """Return True if the page contains positive couplet-content signals."""
    return bool(_BACKREF_PATTERN.search(text) or _DOTTED_GOTO_PATTERN.search(text))
