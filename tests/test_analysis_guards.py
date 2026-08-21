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

# CS/SB 572 swaps a comma for a semicolon next to a clause about legal
# residence and strikes nothing else. It came back summarised as removing a
# residence requirement, across three differently-worded attempts.
SB572_DELETIONS = " , , "

# CS/CS/SB 182 struck "remaining funds have reverted to the state" among other
# fragments, so it is outside what this guard can contradict -- its inverted
# summary is handled upstream, by the marker-direction rules in SYSTEM and by
# the brief no longer splitting one added sentence across four spans.
SB182_DELETIONS = "remaining funds have reverted to the state shall state 1. 2. 3."


def test_a_repeal_against_a_bill_that_strikes_nothing_is_flagged():
    assert flags_false_repeal(
        "Removes the requirement that a relative share a legal residence.",
        SB572_DELETIONS)


def test_the_guard_stays_out_of_it_once_the_bill_strikes_words():
    """Deliberately narrow. Whether the struck words match the claim is a
    judgement, and making it cost four correct summaries out of five."""
    assert not flags_false_repeal(
        "Removes academic dismissal ban, mandates cursive, and eases zoning.",
        SB182_DELETIONS)


def test_a_real_repeal_is_not_flagged():
    """The bill genuinely strikes the duty, so the claim stands."""
    assert not flags_false_repeal(
        "Removes the requirement that districts report annually.",
        "shall report annually to the department")


def test_struck_words_are_enough_even_when_they_do_not_match_the_claim():
    """SB 7004 strikes "shall stand repealed on October 2, 2026" and the model
    called that removing a deadline. Matching the claim's noun against the
    struck language rejected it, and was wrong four times in five."""
    assert not flags_false_repeal(
        "Removes a scheduled deadline that would have ended the privacy "
        "protection.",
        "This paragraph is subject to the Open Government Sunset Review Act "
        "and shall stand repealed on October 2, 2026")


def test_a_removal_achieved_by_adding_language_is_not_flagged():
    """CS/CS/CS/HB 399 eliminates local discretion by ADDING a preemption --
    a real removal that strikes almost nothing."""
    assert not flags_false_repeal(
        "Eliminates local discretion to ban such housing.", "be placed")


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
    is, so SB 686 lost the noun off the end of "for agricultural". Built at
    whatever the cap currently is, so raising it does not quietly disarm this.
    """
    tail = " approval for agricultural"
    cut = "Replaces the comprehensive plan amendment process with direct"
    cut = cut.ljust(ONE_LINE_MAX - len(tail), "x") + tail
    assert len(cut) == ONE_LINE_MAX
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


def test_a_headline_cut_mid_word_is_caught_even_ending_in_a_stop():
    """CS/SB 572 came back "...promote related elected co-." -- at the cap,
    ending in a full stop, and cut mid-word. The hyphen is the tell."""
    tail = " promote related elected co-."
    line = "Expands ethics rules to cover foster families and allows boards to"
    line = line.ljust(ONE_LINE_MAX - len(tail), "x") + tail
    assert len(line) == ONE_LINE_MAX
    assert flags_cut_headline(line)


def test_a_complete_headline_at_the_cap_survives():
    line = "Allows agricultural land to be developed for housing and industry"
    line = line.ljust(ONE_LINE_MAX - len(" without a comprehensive plan."), "x")
    assert not flags_cut_headline(line + " without a comprehensive plan.")


# --- telling a retry what was wrong ----------------------------------------

def test_a_retry_is_told_why_the_last_answer_was_rejected():
    """The server decodes greedily and ignores temperature, so a retry only
    differs if the input does."""
    from flba.analysis.passes import messages, retry_note
    cut = retry_note("headline cut at the 95-character limit ('x')")
    assert "shorter" in cut
    repeal = retry_note("claims a repeal ('removes the ban') but the bill "
                        "deletes no such language")
    assert "[[- -]]" in repeal
    assert cut != repeal
    # anything unrecognised still gets a usable instruction
    assert retry_note("unparseable JSON")


def test_the_note_reaches_the_model_and_only_on_a_retry():
    from flba.analysis.passes import messages
    first = messages("BRIEF", "summary")
    again = messages("BRIEF", "summary", "Your previous answer was too long.")
    assert first[1]["content"].endswith("TASK summary")
    assert "too long" in again[1]["content"]
    # the system prompt is untouched, so the cached prefix still matches
    assert first[0] == again[0]


# --- runaway fields --------------------------------------------------------

def test_every_free_string_in_every_schema_has_a_ceiling():
    """Greedy decoding cannot break out of a repetition loop. On SB 7004 the
    statute field repeated one phrase until it exhausted the token budget,
    three attempts running, and the provision was lost. An enum is bounded by
    its options; anything else needs a maxLength."""
    from flba.analysis.schema import IMPLICATIONS, PROVISIONS, SUMMARY

    unbounded = []

    def walk(node, path):
        if not isinstance(node, dict):
            return
        if node.get("type") == "string" and "enum" not in node:
            if "maxLength" not in node:
                unbounded.append(path)
        for key in ("properties", "items", "$defs"):
            child = node.get(key)
            if isinstance(child, dict):
                if key == "items":
                    walk(child, f"{path}[]")
                else:
                    for name, sub in child.items():
                        walk(sub, f"{path}.{name}")

    for name, schema in (("SUMMARY", SUMMARY), ("PROVISIONS", PROVISIONS),
                         ("IMPLICATIONS", IMPLICATIONS)):
        walk(schema, name)
    assert not unbounded, unbounded


def test_the_caps_sit_above_what_the_model_actually_writes():
    """A ceiling that bites is the headline bug again -- truncation mid-word."""
    from flba.analysis.schema import IMPLICATIONS, PROVISIONS
    prov = PROVISIONS["properties"]["provisions"]["items"]["properties"]
    impl = IMPLICATIONS["properties"]["implications"]["items"]["properties"]
    # longest values observed across 322 provisions and 265 implications
    assert prov["heading"]["maxLength"] > 93
    assert prov["effect"]["maxLength"] > 313
    assert prov["statute"]["maxLength"] > 55
    assert impl["consequence"]["maxLength"] > 568
    assert IMPLICATIONS["properties"]["reading"]["maxLength"] > 1535
    assert IMPLICATIONS["properties"]["unclear"]["items"]["maxLength"] > 766
    from flba.analysis.schema import SUMMARY
    assert SUMMARY["properties"]["who_is_affected"]["items"]["maxLength"] > 165


# --- an amendatory bill has more than one text ------------------------------

def _diff(*parts):
    from flba.diff import Segment, BillDiff
    return BillDiff("html", [Segment(k, t, 1, None) for k, t in parts])


def test_a_quote_of_the_amended_reading_verifies():
    """SB 100 replaces the year inline twelve times, so running the segments
    together reads "Florida Statutes 2026 2025" -- a sentence in no version of
    the law. Quoting what the law will say is quoting the bill."""
    from flba.analysis.brief import readings
    from flba.analysis.verify import Verifier
    d = _diff(("plain", "the title of Florida Statutes"), ("insert", "2026"),
              ("delete", "2025"), ("plain", "and shall take effect"))
    v = Verifier(readings(d))
    assert v.check("the title of Florida Statutes 2026 and shall take effect").ok
    assert v.check("the title of Florida Statutes 2025 and shall take effect").ok


def test_a_quote_spanning_an_edit_still_verifies():
    """The printed page is one of the readings, so a span that crosses an
    edit on both sides is still found."""
    from flba.analysis.brief import readings
    from flba.analysis.verify import Verifier
    d = _diff(("plain", "the title of Florida Statutes"), ("insert", "2026"),
              ("delete", "2025"), ("plain", "and shall take effect"))
    assert Verifier(readings(d)).check("Florida Statutes 2026 2025 and shall").ok


def test_an_invention_still_fails_against_every_reading():
    from flba.analysis.brief import readings
    from flba.analysis.verify import Verifier
    d = _diff(("plain", "the title of Florida Statutes"), ("insert", "2026"),
              ("delete", "2025"))
    v = Verifier(readings(d))
    assert not v.check("local governments may not levy an assessment").ok


def test_a_plain_string_source_still_works():
    """Callers that pass one text keep working."""
    from flba.analysis.verify import Verifier
    assert Verifier("the quick brown fox jumps").check("quick brown fox").ok


# --- a ceiling is not an inverted prohibition -------------------------------

def test_a_cap_permits_everything_under_it():
    """"Compensation may not exceed 125 percent" means a provider may bill up
    to 125 percent. Saying so is a correct reading, not an inverted one. Six
    of the seventeen claims this guard removed were of that shape."""
    assert not flags_inverted_prohibition(
        "A hospital is permitted to bill up to 125% of the Medicare rate.",
        "Compensation to a health care provider may not exceed 125 percent of "
        "the Medicare allowable rate")
    assert not flags_inverted_prohibition(
        "The maximum number of terms a board member can serve rises to three.",
        "a member of the board may not serve for more than three 4-year terms")


def test_a_flat_prohibition_read_as_permission_is_still_caught():
    """CS/CS/SB 118 -- the one claim this guard was right about."""
    assert flags_inverted_prohibition(
        "Assessments can be levied on the entire parking space, including the "
        "area beyond the maximum RV unit size.",
        "may not be levied against the portion of a recreational vehicle "
        "parking space or campsite")


def test_wanting_something_is_not_a_political_motive():
    """Fired twice across 187 bills and was wrong both times: an idiom about
    applicants, and a category of patron the bill defines."""
    assert not flags_intent(
        "Applicants can take the exam as many times as they want in a year.")
    assert not flags_intent(
        "Vendors may sell to patrons who intend to leave with it.")
    # naming an actor still counts
    assert flags_intent("The sponsor wants to expand the exemption.")
    assert flags_intent("Senator Gaetz pushed for this change.")
