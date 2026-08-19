"""Tests for statute cross-referencing."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from flba.diff import INSERT, PLAIN, BillDiff, Segment
from flba.statutes import cites_in, cross_reference, parse_title, statute_url


def diff_of(*pairs):
    return BillDiff("test", [Segment(k, t, i + 1) for i, (k, t) in enumerate(pairs)])


# --- citation extraction --------------------------------------------------

def test_subsection_markers_are_not_harvested_as_statutes():
    """"reenacting s. 493.6201(4), F.S." names one statute, not two.
    Without stripping the parenthetical, the (4) becomes a phantom 's. 4'."""
    assert cites_in("493.6201(4)") == ["493.6201"]


def test_long_enumerations_expand_to_every_section():
    clause = ("489.107(4)(b), 489.113(2), 489.117(1)(a), (2)(a) and (b), "
              "and (4)(a), 489.126(2), 489.131(7)(a)")
    assert cites_in(clause) == ["489.107", "489.113", "489.117",
                                "489.126", "489.131"]


def test_a_section_number_must_contain_a_dot():
    assert cites_in("and (4)(a), (d), and (e)") == []


# --- the formal title -----------------------------------------------------

def test_title_clause_ending_in_fs():
    assert ("amend", "215.422") in parse_title("amending s. 215.422, F.S.;")


def test_title_clause_that_runs_into_relating_to():
    """A long reenactment list often has no 'F.S.' terminator at all --
    requiring one silently drops the whole enumeration."""
    got = parse_title("reenacting ss. 489.107(4)(b), 489.113(2), "
                      "relating to the construction licensing board")
    assert ("reenact", "489.107") in got and ("reenact", "489.113") in got


def test_verbs_map_to_categories():
    assert ("create", "497.1411") in parse_title("creating s. 497.1411, F.S.;")
    assert ("repeal", "322.27") in parse_title("repealing s. 322.27, F.S.;")


# --- section openers ------------------------------------------------------

def test_amended_section_is_located_and_scored():
    d = diff_of((PLAIN, "Section 1. Subsection (15) of section 215.422, "
                        "Florida Statutes, is amended to read:"),
                (INSERT, "new words here"))
    r = cross_reference(d)[0]
    assert (r.statute, r.action, r.bill_section) == ("215.422", "amend", 1)
    assert r.words_added == 3 and r.line_start == 1


def test_created_section():
    d = diff_of((PLAIN, "Section 7. Section 497.1411, Florida Statutes, "
                        "is created to read:"))
    assert cross_reference(d)[0].action == "create"


def test_subsection_added_to_an_existing_statute_counts_as_an_amendment():
    d = diff_of((PLAIN, "Section 3. Subsection (16) is added to section "
                        "493.6102, Florida Statutes, to read:"))
    r = cross_reference(d)[0]
    assert r.statute == "493.6102" and r.action == "amend"


def test_repeal():
    d = diff_of((PLAIN, "Section 9. Section 322.27, Florida Statutes, "
                        "is repealed."))
    assert cross_reference(d)[0].action == "repeal"


def test_whole_chapter_repeal_records_every_named_section():
    """A chapter repeal enumerates its sections. Missing this reports a
    repeal of forty statutes as a repeal of none."""
    d = diff_of((PLAIN, "Section 1. Chapter 205, Florida Statutes, consisting "
                        "of ss. 205.013, 205.022, 205.023, is repealed."))
    refs = cross_reference(d)
    assert {r.statute for r in refs} == {"205.013", "205.022", "205.023"}
    assert all(r.action == "repeal" for r in refs)


def test_statute_url_points_at_the_current_edition():
    assert statute_url("286.011").endswith("/2025/286.011")


def test_empty_bill_yields_nothing():
    assert cross_reference(BillDiff("test", [])) == []
