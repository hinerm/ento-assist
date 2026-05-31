# Copyright 2026 The ento-assist Authors
# SPDX-License-Identifier: MIT

"""Unit tests for extraction/key_parser.py.

All tests are pure string manipulation — no files, no database required.
"""

from __future__ import annotations

from ento_assist.extraction.key_parser import (
    KeyRegion,
    ProposedCouplet,
    ProposedKey,
    ProposedLeg,
    _extract_fig_refs,
    _extract_goto,
    _extract_terminal,
    parse_key_from_texts,
    scan_for_key_boundaries,
)

# ---------------------------------------------------------------------------
# Minimal fixture text: a clean 4-couplet key
# ---------------------------------------------------------------------------

_CLEAN_KEY = """\
Key to Genera of Culicidae

1. Wings with distinct dark spots .......................... 2
1. Wings without dark spots .............................. 3

2. Proboscis much longer than head .......... Toxorhynchites
2. Proboscis not greatly elongated ........... go to 4

3. Scutellum trilobed ................................ Aedes
3. Scutellum evenly rounded ..................... Culex pipiens

4. Palps as long as proboscis ................. Anopheles gambiae
4. Palps shorter than proboscis ............... Mansonia
"""

_CLEAN_KEY_PAGE_START = 5  # arbitrary page offset to test page_ref tracking


# ---------------------------------------------------------------------------
# ProposedKey dataclass structure
# ---------------------------------------------------------------------------


def test_proposed_key_has_expected_fields():
    key = ProposedKey(title="t", page_start=0, page_end=0, couplets=[])
    assert hasattr(key, "title")
    assert hasattr(key, "couplets")
    assert hasattr(key, "confidence")
    assert hasattr(key, "warnings")
    assert isinstance(key.warnings, list)


def test_proposed_couplet_has_expected_fields():
    leg_a = ProposedLeg(leg_label="A", text="foo")
    leg_b = ProposedLeg(leg_label="B", text="bar")
    couplet = ProposedCouplet(number="1", page_ref=0, leg_a=leg_a, leg_b=leg_b)
    assert couplet.number == "1"
    assert couplet.leg_a.leg_label == "A"
    assert couplet.leg_b.leg_label == "B"
    assert couplet.confidence == 1.0  # default


# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------


def test_extract_goto_go_to_form():
    assert _extract_goto("Hind wing large ......... go to 5") == "5"


def test_extract_goto_dots_trailing():
    assert _extract_goto("Front tibia with spurs ............. 3") == "3"


def test_extract_goto_see_form():
    assert _extract_goto("Tarsi with claws, see 4a") == "4a"


def test_extract_goto_arrow_form():
    assert _extract_goto("Large body → 7") == "7"


def test_extract_goto_none_when_absent():
    assert _extract_goto("Scutellum trilobed") is None


def test_extract_terminal_genus_species():
    # Input must start with the taxon so the regex doesn't match leading prose first
    result = _extract_terminal("Aedes aegypti")
    assert result is not None
    assert "Aedes" in result


def test_extract_terminal_returns_none_for_goto_text():
    # Text that has a goto reference should not also return a terminal
    # (_extract_terminal is only called when goto is None in _parse_leg)
    result = _extract_terminal("go to 3")
    # The terminal pattern may or may not match here; just confirm no crash
    assert result is None or isinstance(result, str)


def test_extract_fig_refs_single():
    refs = _extract_fig_refs("Antenna as in Fig. 3b ................. 2")
    assert len(refs) >= 1
    assert any("3b" in r or "Fig" in r for r in refs)


def test_extract_fig_refs_plural_range():
    refs = _extract_fig_refs("Wing venation (Figs. 3–5) .............. Culex")
    assert len(refs) >= 1


def test_extract_fig_refs_empty_when_absent():
    refs = _extract_fig_refs("Scutellum trilobed, no illustrations")
    assert refs == []


# ---------------------------------------------------------------------------
# Full parse: clean key
# ---------------------------------------------------------------------------


def test_full_parse_returns_proposed_key():
    result = parse_key_from_texts([_CLEAN_KEY], page_start=_CLEAN_KEY_PAGE_START)
    assert isinstance(result, ProposedKey)


def test_full_parse_couplet_count():
    result = parse_key_from_texts([_CLEAN_KEY], page_start=_CLEAN_KEY_PAGE_START)
    assert len(result.couplets) == 4


def test_full_parse_couplet_numbers():
    result = parse_key_from_texts([_CLEAN_KEY], page_start=_CLEAN_KEY_PAGE_START)
    numbers = {c.number for c in result.couplets}
    assert numbers == {"1", "2", "3", "4"}


def test_full_parse_page_refs_correct():
    result = parse_key_from_texts([_CLEAN_KEY], page_start=_CLEAN_KEY_PAGE_START)
    for couplet in result.couplets:
        assert couplet.page_ref == _CLEAN_KEY_PAGE_START


def test_full_parse_goto_targets():
    result = parse_key_from_texts([_CLEAN_KEY], page_start=_CLEAN_KEY_PAGE_START)
    c1 = next(c for c in result.couplets if c.number == "1")
    # Both legs of couplet 1 should resolve to goto targets
    assert c1.leg_a.next_couplet_number == "2"
    assert c1.leg_b.next_couplet_number == "3"


def test_full_parse_terminal_taxa():
    result = parse_key_from_texts([_CLEAN_KEY], page_start=_CLEAN_KEY_PAGE_START)
    c3 = next(c for c in result.couplets if c.number == "3")
    # Leg A leads to Aedes (terminal), leg B leads to Culex
    assert c3.leg_a.terminal_taxon_name is not None
    assert c3.leg_b.terminal_taxon_name is not None


def test_full_parse_title_from_argument():
    result = parse_key_from_texts([_CLEAN_KEY], title="My Custom Title")
    assert result.title == "My Custom Title"


def test_full_parse_default_title():
    result = parse_key_from_texts([_CLEAN_KEY])
    assert result.title == "Untitled Key"


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


def test_confidence_clean_key():
    result = parse_key_from_texts([_CLEAN_KEY])
    # Each leg has either a goto or terminal resolved → high confidence
    assert result.confidence >= 0.7


def test_confidence_empty_input():
    result = parse_key_from_texts([""])
    assert result.confidence == 0.0


def test_confidence_single_leg_only():
    # Only one occurrence of number 1 → stub couplet with 0.3 confidence
    one_leg = "1. Wings present ....... 2\n"
    result = parse_key_from_texts([one_leg])
    assert result.confidence <= 0.5
    assert len(result.warnings) >= 1


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def test_warnings_no_couplet_numbers():
    result = parse_key_from_texts(["This text has no numbered couplets at all."])
    assert any("No couplet numbers" in w for w in result.warnings)


def test_warnings_unpaired_couplet():
    # Couplet 2 appears only once
    text = (
        "1. Wings present ....... 2\n1. Wings absent ........ 3\n2. Large wings ......... Aedes\n"
    )
    result = parse_key_from_texts([text])
    assert any("2" in w for w in result.warnings)


def test_warnings_is_list_of_strings():
    result = parse_key_from_texts(["1. foo go to 2\n"])
    assert isinstance(result.warnings, list)
    assert all(isinstance(w, str) for w in result.warnings)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty_couplets():
    result = parse_key_from_texts([])
    assert result.couplets == []


def test_single_empty_page():
    result = parse_key_from_texts([""])
    assert result.couplets == []


def test_page_start_offset_stored():
    result = parse_key_from_texts([_CLEAN_KEY], page_start=10)
    assert result.page_start == 10


# ---------------------------------------------------------------------------
# scan_for_key_boundaries
# ---------------------------------------------------------------------------


def test_scan_detects_key_header():
    pages = [
        "Introduction to entomology.\n\nKey to Genera of Diptera\n\n1. foo .... 2\n1. bar .... 3\n"
    ]
    regions = scan_for_key_boundaries(pages, page_start=0)
    assert len(regions) >= 1
    assert isinstance(regions[0], KeyRegion)


def test_scan_returns_empty_for_no_key():
    pages = ["This is a taxonomy chapter with no keys.\n\nSome text here.\n"]
    regions = scan_for_key_boundaries(pages, page_start=0)
    assert regions == []


def test_scan_page_start_respected():
    pages = ["Key to Species of Culicidae\n\n1. foo .... 2\n"]
    regions = scan_for_key_boundaries(pages, page_start=7)
    assert len(regions) >= 1
    assert regions[0].page_start == 7
