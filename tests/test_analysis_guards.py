"""Guards on what the analysis is allowed to publish."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flba.analysis.brief import render_passage
from flba.analysis.passes import (flags_cut_headline, flags_false_repeal,
                                  flags_intent, flags_inverted_prohibition)
from flba.analysis.schema import ONE_LINE_MAX
from flba.diff import DELETE, INSERT, PLAIN, Segment
from flba.analysis.verify import Verifier, is_degenerate, verify_claims

SRC = ("The non-ad valorem special assessment may not be levied against the "
       "portion of a recreational vehicle parking space or campsite which "
       "exceeds the maximum square footage of a recreational vehicle.")


# --- reading a prohibition as a permission ---------------------------------

def test_may_not_is_a_prohibition_not_a_permission():
    """Florida writes prohibitions as "may not" far more often than "shall
    not". A model treating "may not" as the weaker form inverts the bill."""
    claim = ("A local government can levy the assessment on the entire area, "
             "because the prohibition is phrased as a permission.")
    assert flags_inverted_prohibition(claim, SRC)


def test_correctly_reading_the_prohibition_is_not_flagged():
    assert not flags_inverted_prohibition(
        "A local government can no longer levy the assessment on that portion.",
        SRC)
    assert not flags_inverted_prohibition(
        "Local governments are prohibited from levying on that portion.", SRC)


def test_a_genuine_permission_is_not_flagged():
    assert not flags_inverted_prohibition(
        "A school district may adopt rules for the program.",
        "the district school board may adopt rules")


def test_shall_not_is_caught_too():
    assert flags_inverted_prohibition(
        "Agencies are allowed to withhold the record indefinitely.",
        "an agency shall not withhold the record beyond 30 days")


# --- motive speculation ----------------------------------------------------

def test_motive_claims_are_flagged():
    assert flags_intent("The sponsor intends to weaken oversight.")
    assert flags_intent("Republicans who want to limit local control.")


def test_representative_is_a_title_only_before_a_name():
    """A bill on unclaimed property speaks of claimants' representatives --
    agents, not members of the House."""
    assert not flags_intent("Claimants' representatives must be registered.")
    assert not flags_intent("An authorized representative may sign for the owner.")
    assert flags_intent("Senator Gaetz wants to expand the exemption.")
    assert flags_intent("Rep. Smith pushed for this change.")


def test_innocent_intent_words_are_not_flagged():
    """Legal drafting is full of them: "a rural study area intended for
    residential development" is a designation, not a motive."""
    assert not flags_intent("a rural study area intended for residential development")
    assert not flags_intent("Section 4 permits an agency to act without notice.")


# --- quotes ----------------------------------------------------------------

def test_a_paraphrase_does_not_verify():
    kept, dropped = verify_claims(
        [{"quote": "local governments cannot charge for the extra area"}],
        Verifier(SRC))
    assert not kept and dropped


def test_an_exact_span_verifies():
    kept, _ = verify_claims(
        [{"quote": "may not be levied against the portion of a recreational vehicle"}],
        Verifier(SRC))
    assert kept and kept[0]["quote_status"] == "exact"


def test_padded_output_is_rejected():
    assert is_degenerate('{"reading": "}' + "\n" * 60)


# --- claiming a repeal the bill never makes --------------------------------

# CS/CS/SB 182 added a prohibition on academic dismissal from charter schools
# and deleted nothing but "shall", "state" and three list numbers. It came
# back summarised as removing an "academic dismissal ban".
SB182_DELETIONS = "remaining funds have reverted to the state shall state 1. 2. 3."


def test_a_repeal_the_bill_never_makes_is_flagged():
    assert flags_false_repeal(
        "Removes academic dismissal ban, mandates cursive, and eases zoning.",
        SB182_DELETIONS)


def test_a_real_repeal_is_not_flagged():
    """The bill genuinely strikes the duty, so the claim stands."""
    assert not flags_false_repeal(
        "Removes the requirement that districts report annually.",
        "shall report annually to the department")


def test_a_repeal_of_a_prohibition_needs_a_struck_prohibition():
    struck = "a school district may not charge a fee for the transfer"
    assert not flags_false_repeal("Lifts the ban on transfer fees.", struck)
    assert flags_false_repeal("Lifts the ban on transfer fees.", "shall 1. 2.")


def test_ordinary_summaries_are_not_flagged():
    """Every guard here has failed by being too eager, so the common shapes
    of an honest summary must pass untouched."""
    for line in (
        "Requires public schools to teach cursive writing in grades 3 through 5.",
        "Charter schools may not dismiss students for poor grades.",
        "Creates a new exemption for data centers and removes a tax credit cap.",
        "Bars local governments from levying the assessment.",
    ):
        assert not flags_false_repeal(line, ""), line


# --- one marker per edit, not one per line ---------------------------------

def test_a_multi_line_edit_becomes_one_span():
    """The parser emits a segment per line of the Legislature's layout. Four
    consecutive INSERT segments are one added sentence, not four."""
    parts = [Segment(PLAIN, "school.", 84, None),
             Segment(INSERT, "A student may not be dismissed based on academic", 84, None),
             Segment(INSERT, "performance while a school is implementing a school", 85, None),
             Segment(INSERT, "improvement plan pursuant to s. 1002.345.", 86, None),
             Segment(PLAIN, "6. Students articulating", 88, None)]
    out = render_passage(parts)
    assert out.count("[[+") == 1
    assert ("[[+A student may not be dismissed based on academic performance "
            "while a school is implementing a school improvement plan "
            "pursuant to s. 1002.345.+]]") in out


def test_runs_of_different_kinds_stay_separate():
    parts = [Segment(INSERT, "new one", 1, None), Segment(INSERT, "new two", 2, None),
             Segment(DELETE, "old one", 3, None), Segment(INSERT, "new three", 4, None)]
    out = render_passage(parts)
    assert out == "[[+new one new two+]] [[-old one-]] [[+new three+]]"


# --- a headline cut off at the schema's cap ---------------------------------

def test_a_headline_cut_at_the_cap_is_rejected():
    """Constrained decoding closes the string at maxLength wherever the model
    is, so SB 686 lost the noun off the end of "for agricultural"."""
    cut = "Replaces comprehensive plan amendment process with direct development approval for agricultural"
    assert len(cut) <= ONE_LINE_MAX
    assert flags_cut_headline(cut)


def test_a_complete_headline_at_the_cap_is_kept():
    """Length alone is not the fault -- stopping mid-phrase is."""
    line = "Bars local governments from levying assessments on recreational vehicle parking spaces."
    assert not flags_cut_headline(line)


def test_a_short_headline_is_never_flagged():
    assert not flags_cut_headline("Mandates cursive instruction in grades 3 through 5")
    assert not flags_cut_headline("")


def test_the_schema_no_longer_spells_out_a_bad_example():
    """A negative example written out in full is a phrase the model copies."""
    from flba.analysis.schema import SUMMARY
    desc = SUMMARY["properties"]["one_line"]["description"]
    assert "Bad:" not in desc
    assert "Good:" in desc
