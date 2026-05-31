"""Dichotomous key parser for ento-assist.

Converts raw page text into a proposed key structure for human review.
This module does NOT write to the database — it returns a ProposedKey
data structure that goes through the ingestion review workflow before
being committed.

Design philosophy:
    Entomological key layouts vary enormously between authors and publishers.
    This parser uses heuristics and returns confidence scores; it is
    intentionally conservative. Human review of the output is ALWAYS required
    before committing to the database.

Public API:
    parse_key_from_texts(page_texts, page_start) -> ProposedKey
    scan_for_key_boundaries(page_texts, page_start) -> list[KeyRegion]
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ProposedLeg:
    """One side (A or B) of a proposed couplet."""

    leg_label: str              # 'A' or 'B'
    text: str
    next_couplet_number: Optional[str] = None   # raw goto ref, e.g. "4", "4a"
    terminal_taxon_name: Optional[str] = None   # if this leg ends the key
    figure_references: list[str] = field(default_factory=list)  # e.g. ["Fig. 3b"]
    confidence: float = 1.0     # 0.0–1.0; lower = needs careful review


@dataclass
class ProposedCouplet:
    """A proposed couplet parsed from source text."""

    number: str                 # original label from text, e.g. "1", "1a"
    page_ref: int               # page number (0-indexed) where this couplet appears
    leg_a: ProposedLeg
    leg_b: ProposedLeg
    confidence: float = 1.0     # overall couplet confidence (min of leg confidences)
    raw_text: str = ""          # original text block for review display


@dataclass
class ProposedKey:
    """The full proposed structure for a single dichotomous key."""

    title: str
    page_start: int
    page_end: int
    couplets: list[ProposedCouplet]
    warnings: list[str] = field(default_factory=list)
    # Overall parse confidence: mean of all couplet confidences
    confidence: float = 1.0


@dataclass
class KeyRegion:
    """A detected key region within a document, before parsing."""

    title: str
    page_start: int
    page_end: int
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------

# Patterns that commonly indicate the start of a dichotomous key
_KEY_HEADER_PATTERNS = [
    re.compile(r"\bkey\s+to\b", re.IGNORECASE),
    re.compile(r"\bdetermination\s+key\b", re.IGNORECASE),
    re.compile(r"\bidentification\s+key\b", re.IGNORECASE),
    re.compile(r"\bcouplet\s+1\b", re.IGNORECASE),
]

# Couplet number line: "1.", "1a.", "1(1).", etc. at the start of a line
_COUPLET_NUM_PATTERN = re.compile(
    r"^(?P<num>\d+[a-zA-Z]?(?:\(\d+\))?)[.)]\s+(?P<text>.+)", re.MULTILINE
)

# Figure reference patterns: "Fig. 3b", "fig. 12", "Figs. 3–5", etc.
_FIGURE_REF_PATTERN = re.compile(
    r"\bfig(?:s?|ure(?:s)?)[.\s]+[\d\w–\-,\s]+", re.IGNORECASE
)

# "goto" patterns: "...........3", "see 4a", "go to 5"
_GOTO_PATTERN = re.compile(
    r"(?:go\s+to|see|→|->)\s*(\d+[a-zA-Z]?)"
    r"|[.]{3,}\s*(\d+[a-zA-Z]?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Taxon terminal patterns: italicised genus+species, common family endings
_TERMINAL_PATTERN = re.compile(
    r"\b[A-Z][a-z]+(?:\s+[a-z]+){1,3}\b"  # Genus species [subsp]
    r"|\b\w+(?:inae|idae|ini)\b",           # subfamily/family/tribe endings
)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def scan_for_key_boundaries(
    page_texts: list[str],
    page_start: int = 0,
) -> list[KeyRegion]:
    """Scan page texts to detect likely key regions.

    Returns a list of KeyRegion objects with estimated page ranges. These are
    presented to the user for confirmation before `parse_key_from_texts` is
    called on each confirmed region.

    Args:
        page_texts: List of text strings, one per page, in order.
        page_start: The page number (0-indexed) of the first entry in page_texts.

    Returns:
        List of detected KeyRegion objects, sorted by page number.
    """
    regions: list[KeyRegion] = []
    in_key = False
    key_start_page = 0
    key_title = ""

    for i, text in enumerate(page_texts):
        abs_page = page_start + i

        # Detect key headers
        header_match = None
        for pat in _KEY_HEADER_PATTERNS:
            m = pat.search(text)
            if m:
                header_match = m
                break

        if header_match and not in_key:
            in_key = True
            key_start_page = abs_page
            # Extract surrounding line as title candidate
            line_start = text.rfind("\n", 0, header_match.start())
            line_end = text.find("\n", header_match.end())
            title_line = text[line_start + 1 : line_end if line_end != -1 else None]
            key_title = title_line.strip()[:120]

        # Detect potential key end: a new section header that isn't a key
        elif in_key and _looks_like_new_section(text) and not header_match:
            regions.append(
                KeyRegion(
                    title=key_title or f"Key on page {key_start_page + 1}",
                    page_start=key_start_page,
                    page_end=abs_page - 1,
                )
            )
            in_key = False

    # Close any open region at end of supplied pages
    if in_key:
        regions.append(
            KeyRegion(
                title=key_title or f"Key on page {key_start_page + 1}",
                page_start=key_start_page,
                page_end=page_start + len(page_texts) - 1,
            )
        )

    return regions


def parse_key_from_texts(
    page_texts: list[str],
    page_start: int = 0,
    title: str = "",
) -> ProposedKey:
    """Parse a dichotomous key from a list of page texts.

    This function attempts to identify couplets by looking for numbered
    paragraph pairs. It is intentionally conservative: ambiguous cases get
    low confidence scores so that human reviewers know to check them.

    Args:
        page_texts: List of text strings, one per page, in order.
        page_start: The page number (0-indexed) of the first entry in page_texts.
        title: Optional title for the key.

    Returns:
        A ProposedKey containing all parsed couplets and any warnings.
    """
    warnings: list[str] = []
    couplets: list[ProposedCouplet] = []

    # Join all pages with a page-break sentinel so we can track page refs
    full_text_parts: list[tuple[int, str]] = []  # (page_num, text)
    for i, text in enumerate(page_texts):
        full_text_parts.append((page_start + i, text))

    # Build a flat list of numbered lines with their page refs
    numbered_lines: list[tuple[str, str, int]] = []  # (number, text, page_num)
    for page_num, page_text in full_text_parts:
        for m in _COUPLET_NUM_PATTERN.finditer(page_text):
            numbered_lines.append((m.group("num"), m.group("text").strip(), page_num))

    if not numbered_lines:
        warnings.append(
            "No couplet numbers detected. The key may use an unusual format "
            "or the text layer may be poor quality. Consider OCR fallback."
        )
        return ProposedKey(
            title=title,
            page_start=page_start,
            page_end=page_start + len(page_texts) - 1,
            couplets=[],
            warnings=warnings,
            confidence=0.0,
        )

    # Group consecutive same-numbered lines as A/B pairs
    # (e.g. "1." appears twice: first instance = A, second = B)
    seen: dict[str, list[tuple[str, int]]] = {}
    for num, text, page_num in numbered_lines:
        seen.setdefault(num, []).append((text, page_num))

    for num, entries in seen.items():
        if len(entries) < 2:
            warnings.append(
                f"Couplet {num}: only one leg detected — may be split across pages "
                "or misformatted."
            )
            # Create a stub with low confidence
            leg_a_text, leg_a_page = entries[0]
            couplet = ProposedCouplet(
                number=num,
                page_ref=leg_a_page,
                leg_a=ProposedLeg(
                    leg_label="A",
                    text=leg_a_text,
                    figure_references=_extract_fig_refs(leg_a_text),
                    confidence=0.3,
                ),
                leg_b=ProposedLeg(
                    leg_label="B",
                    text="[NOT DETECTED — REQUIRES MANUAL ENTRY]",
                    confidence=0.0,
                ),
                confidence=0.3,
                raw_text=leg_a_text,
            )
            couplets.append(couplet)
            continue

        if len(entries) > 2:
            warnings.append(
                f"Couplet {num}: {len(entries)} lines detected (expected 2). "
                "Using first two; others may be continuation text."
            )

        leg_a_text, leg_a_page = entries[0]
        leg_b_text, leg_b_page = entries[1]

        leg_a = _parse_leg("A", leg_a_text, leg_a_page)
        leg_b = _parse_leg("B", leg_b_text, leg_b_page)

        couplet_conf = min(leg_a.confidence, leg_b.confidence)
        couplets.append(
            ProposedCouplet(
                number=num,
                page_ref=leg_a_page,
                leg_a=leg_a,
                leg_b=leg_b,
                confidence=couplet_conf,
                raw_text=f"{leg_a_text}\n{leg_b_text}",
            )
        )

    # Sort couplets by their numeric value for display
    def _sort_key(c: ProposedCouplet) -> tuple[int, str]:
        digits = re.match(r"(\d+)", c.number)
        return (int(digits.group(1)) if digits else 0, c.number)

    couplets.sort(key=_sort_key)

    overall_conf = (
        sum(c.confidence for c in couplets) / len(couplets) if couplets else 0.0
    )

    return ProposedKey(
        title=title or "Untitled Key",
        page_start=page_start,
        page_end=page_start + len(page_texts) - 1,
        couplets=couplets,
        warnings=warnings,
        confidence=overall_conf,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_leg(label: str, text: str, page_num: int) -> ProposedLeg:
    """Parse a single couplet leg from its text."""
    fig_refs = _extract_fig_refs(text)
    goto = _extract_goto(text)
    terminal = _extract_terminal(text) if goto is None else None

    confidence = 1.0
    if not text.strip():
        confidence = 0.0
    elif goto is None and terminal is None:
        # No resolution found — may be continued on next line or misformatted
        confidence = 0.5

    return ProposedLeg(
        leg_label=label,
        text=text,
        next_couplet_number=goto,
        terminal_taxon_name=terminal,
        figure_references=fig_refs,
        confidence=confidence,
    )


def _extract_fig_refs(text: str) -> list[str]:
    return [m.group(0).strip() for m in _FIGURE_REF_PATTERN.finditer(text)]


def _extract_goto(text: str) -> Optional[str]:
    m = _GOTO_PATTERN.search(text)
    if m:
        return (m.group(1) or m.group(2) or "").strip() or None
    return None


def _extract_terminal(text: str) -> Optional[str]:
    m = _TERMINAL_PATTERN.search(text)
    return m.group(0).strip() if m else None


def _looks_like_new_section(text: str) -> bool:
    """Heuristic: does this page look like it starts a new major section?"""
    # Look for all-caps headings or lines that are short and title-cased near the top
    first_500 = text[:500]
    lines = [l.strip() for l in first_500.splitlines() if l.strip()]
    if not lines:
        return False
    first_line = lines[0]
    return (
        first_line.isupper() and len(first_line) > 4
    ) or (
        first_line.istitle() and len(first_line.split()) <= 6
    )
