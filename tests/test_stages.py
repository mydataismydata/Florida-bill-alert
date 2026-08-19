"""Tests for the stage/pathway tracker.

The first group guards the three ways the Florida action vocabulary misleads a
naive reader. Each of these was a real bug before it was a test.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from flba.stages import (ADOPTED, BILL, DIED, JOINT, LAW, PENDING, RESOLUTION,
                         SUPERSEDED, TO_BALLOT, VETOED, is_terminal, kind_of,
                         pathway, track)

DB = ROOT / "data" / "index.sqlite"


def ev(*actions, chamber="House"):
    return [{"date": "1/1/2026", "chamber": chamber, "action": a} for a in actions]


# --- the three traps ------------------------------------------------------

def test_laid_on_table_under_rule_is_a_substitution_not_a_death():
    """A committee substitute tables the version it replaces; the bill lives.
    107 bills with this action in 2026 went on to become law."""
    p = track(ev("Filed",
                 "Favorable with CS by Criminal Justice Subcommittee",
                 "Laid on Table under Rule 7.18(a)",
                 "CS Filed",
                 "Referred to Judiciary Committee"))
    assert p.outcome == PENDING
    assert not is_terminal("Laid on Table under Rule 7.18(a)")


def test_withdrawn_from_a_committee_is_acceleration_not_a_death():
    """It pulls a bill onto the calendar. In 2026 this was followed by
    'Placed on Calendar' 157 times out of 164."""
    p = track(ev("Filed", "Referred to Rules",
                 "Withdrawn from Rules",
                 "Placed on Calendar, on 2nd reading"))
    assert p.outcome == PENDING
    assert p.stage == "on_calendar"


def test_withdrawn_prior_to_introduction_really_is_a_death():
    assert track(ev("Filed", "Withdrawn prior to introduction")).outcome == DIED


def test_death_with_a_passing_companion_is_reported_as_superseded():
    """The policy was enacted through the twin bill. Calling this a plain
    failure misinforms the reader."""
    p = track(ev("Filed",
                 "Died in Rules, companion bill(s) passed, see CS/CS/HB 123"))
    assert p.outcome == SUPERSEDED
    assert p.companion == "CS/CS/HB 123"


# --- outcomes -------------------------------------------------------------

def test_chapter_number_means_it_became_law():
    p = track(ev("Filed", "Approved by Governor", "Chapter No. 2026-174"))
    assert p.outcome == LAW and p.stage == "law" and p.percent == 100


def test_veto_is_distinct_from_death():
    p = track(ev("Filed", "Signed by Officers and presented to Governor",
                 "Vetoed by Governor"))
    assert p.outcome == VETOED
    assert p.stage == "to_governor", "a vetoed bill still reached the Governor"


def test_died_in_names_the_committee():
    assert track(ev("Filed", "Died in Ways & Means Committee")).died_in == \
        "Ways & Means Committee"


def test_a_later_death_does_not_overwrite_becoming_law():
    p = track(ev("Filed", "Approved by Governor", "Chapter No. 2026-1",
                 "Laid on Table, companion bill(s) passed, see HB 9"))
    assert p.outcome == LAW


# --- bill kinds -----------------------------------------------------------

def test_kinds_are_read_through_substitute_prefixes():
    assert kind_of("CS/CS/HJR 203") == JOINT
    assert kind_of("HB 33") == BILL
    assert kind_of("SR 1466") == RESOLUTION


def test_resolutions_are_adopted_not_enacted():
    p = track(ev("Filed", "Adopted by Publication"), kind=RESOLUTION)
    assert p.outcome == ADOPTED


def test_a_passed_joint_resolution_goes_to_the_voters():
    """Constitutional amendments bypass the Governor and reach the ballot."""
    p = track(ev("Filed", "Signed by Officers and filed with Secretary of State"),
              kind=JOINT)
    assert p.outcome == TO_BALLOT


def test_a_resolution_is_scored_against_its_own_shorter_ladder():
    p = track(ev("Filed", "Adopted"), kind=RESOLUTION)
    assert [s["label"] for s in pathway(p)][-1] == \
        "Filed with the Secretary of State"


# --- pathway --------------------------------------------------------------

def test_pathway_marks_every_rung_up_to_the_current_one():
    p = track(ev("Filed", "Referred to Rules", "Read 2nd time"))
    steps = pathway(p)
    assert [s["reached"] for s in steps][:6] == [True] * 6
    assert steps[5]["current"] and not steps[6]["reached"]


# --- corpus regression ----------------------------------------------------

@pytest.mark.skipif(not DB.exists(), reason="no ingested corpus")
def test_no_enacted_bill_is_ever_reported_as_dead():
    """The whole 2026 session, classified from history alone -- the chapter
    number is withheld so the state machine cannot cheat."""
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    bills = db.execute(
        "SELECT num,label,chapter_law FROM bill WHERE session='2026'").fetchall()
    if not bills:
        pytest.skip("2026 not ingested")
    hist: dict[int, list] = {}
    for r in db.execute("SELECT num,date,chamber,action FROM history"
                        " WHERE session='2026' ORDER BY num,seq"):
        hist.setdefault(r["num"], []).append(dict(r))

    enacted = wrong = 0
    for b in bills:
        p = track(hist.get(b["num"], []), kind=kind_of(b["label"]))
        if b["chapter_law"]:
            enacted += 1
            if p.outcome != LAW:
                wrong += 1
        elif p.outcome == LAW:
            wrong += 1
    assert enacted == 228, f"expected 228 enacted bills, found {enacted}"
    assert wrong == 0, f"{wrong} bills misclassified against the chapter-law record"
