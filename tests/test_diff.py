"""Tests for the additions/deletions parser.

The geometry tests use synthetic pages so they run anywhere. The corpus tests
need a populated raw cache and skip cleanly without one.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from flba.diff import (DELETE, INSERT, PLAIN, BillDiff, Segment, _classify,
                       normalize, parse_house_pdf, parse_senate_html)

DB = ROOT / "data" / "index.sqlite"


def word(x0, x1, top=100.0, bottom=110.0, text="word"):
    return {"x0": x0, "x1": x1, "top": top, "bottom": bottom, "text": text}


def rule(x0, x1, top, height=0.5):
    return {"x0": x0, "x1": x1, "top": top, "height": height,
            "width": x1 - x0, "y0": 0, "y1": 0}


# --- geometry: the rule's height decides addition vs deletion --------------

def test_underline_below_baseline_is_an_addition():
    assert _classify(word(10, 50), [rule(10, 50, top=110.4)]) == INSERT


def test_rule_through_the_glyphs_is_a_deletion():
    assert _classify(word(10, 50), [rule(10, 50, top=105.3)]) == DELETE


def test_unmarked_word_is_plain():
    assert _classify(word(10, 50), []) == PLAIN


def test_rule_on_the_line_below_does_not_mark_this_word():
    assert _classify(word(10, 50), [rule(10, 50, top=140.0)]) == PLAIN


def test_rule_grazing_a_word_does_not_mark_it():
    # only ~12% of the word is covered; below the coverage threshold
    assert _classify(word(10, 50), [rule(10, 15, top=110.4)]) == PLAIN


def test_thick_rect_is_furniture_not_a_rule():
    from flba.diff import _rules

    class P:
        rects = [rule(10, 50, top=110.4, height=6.0)]
        lines = []
    assert _rules(P()) == []


# --- Senate HTML ----------------------------------------------------------

def test_senate_runs_split_mid_word_are_rejoined():
    # the generator really does split words across adjacent tags
    html = '<pre>x <u class="Insert">applica</u><u class="Insert">bility</u> y</pre>'
    d = parse_senate_html(html)
    assert "applicability" in [s.text for s in d.insertions]


def test_senate_insert_and_remove_are_distinguished():
    html = ('<pre><u class="Insert">new words</u> keep '
            '<s class="Remove">old words</s></pre>')
    d = parse_senate_html(html)
    assert [s.text for s in d.insertions] == ["new words"]
    assert [s.text for s in d.deletions] == ["old words"]


def test_senate_line_numbers_are_captured_and_not_treated_as_text():
    html = ('<pre>\n    1         An act relating to '
            '<u class="Insert">public notice</u>;\n'
            '    2         amending s. 286.011, F.S.;\n</pre>')
    d = parse_senate_html(html)
    assert [s.line for s in d.insertions] == [1]
    assert "1" not in d.words(INSERT)


# --- tokenisation ---------------------------------------------------------

def test_curly_apostrophe_matches_ascii():
    assert normalize("applicant’s") == "applicant's"


def test_line_wrapped_word_is_stitched():
    d = BillDiff("t", [Segment(INSERT, "end-of-", 1), Segment(INSERT, "course", 2)])
    assert d.words(INSERT) == ["end-of-course"]


def test_possessive_is_one_word():
    d = BillDiff("t", [Segment(DELETE, "the applicant’s form")])
    assert d.words(DELETE) == ["the", "applicant's", "form"]


# --- corpus regression ----------------------------------------------------

requires_cache = pytest.mark.skipif(
    not DB.exists(), reason="no ingested corpus; run `flba bills` first")


def _text_path(num, fmt):
    db = sqlite3.connect(DB)
    row = db.execute(
        "SELECT url FROM bill_text WHERE session='2026' AND num=? AND fmt=?"
        " AND version LIKE '%Filed%' LIMIT 1", (num, fmt)).fetchone()
    if not row:
        return None
    from flba.http import PoliteFetcher
    p = PoliteFetcher(ROOT / "data" / "raw" / "flsenate").local_path(row[0])
    return p if p.exists() else None


@requires_cache
def test_house_pdf_yields_both_marks():
    path = _text_path(1277, "PDF")
    if not path:
        pytest.skip("HB 1277 text not cached")
    d = parse_house_pdf(path)
    assert d.stats()["words_inserted"] > 100
    assert d.stats()["words_deleted"] > 1000
    assert any(s.line for s in d.insertions), "line numbers must be captured"


@requires_cache
def test_house_pdf_matches_its_identical_senate_companion():
    """HB 1277 and SB 552 were filed as identical bills."""
    import difflib
    hp, sp = _text_path(1277, "PDF"), _text_path(552, "HTML")
    if not (hp and sp):
        pytest.skip("companion pair not cached")
    hd = parse_house_pdf(hp)
    sd = parse_senate_html(sp.read_text(encoding="utf-8", errors="replace"))
    norm = lambda ws: [w.lower().strip(".,;:()") for w in ws if w.strip(".,;:()")]
    for kind in (INSERT, DELETE):
        ratio = difflib.SequenceMatcher(
            None, norm(hd.words(kind)), norm(sd.words(kind))).ratio()
        assert ratio > 0.95, f"{kind} similarity fell to {ratio:.1%}"


@requires_cache
def test_both_chambers_cite_line_numbers():
    """Line numbers are how amendments reference bill text, so both paths
    must carry them."""
    hp, sp = _text_path(1277, "PDF"), _text_path(552, "HTML")
    if not (hp and sp):
        pytest.skip("companion pair not cached")
    for d in (parse_house_pdf(hp),
              parse_senate_html(sp.read_text(encoding="utf-8", errors="replace"))):
        marked = d.insertions + d.deletions
        assert marked, f"{d.source} found nothing"
        with_lines = [s for s in marked if s.line]
        assert len(with_lines) / len(marked) > 0.9, \
            f"{d.source} only located {len(with_lines)}/{len(marked)} segments"


@requires_cache
def test_legend_is_not_mistaken_for_content():
    """Every page footer says 'words stricken ... words underlined', itself
    struck and underlined. Leaving it in would add a false hit per page."""
    path = _text_path(1277, "PDF")
    if not path:
        pytest.skip("HB 1277 text not cached")
    d = parse_house_pdf(path)
    joined = " ".join(s.text for s in d.insertions + d.deletions).lower()
    assert "are deletions" not in joined
    assert "coding:" not in joined
