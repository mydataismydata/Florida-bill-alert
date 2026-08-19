"""Guards on what the analysis is allowed to publish."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flba.analysis.passes import flags_intent, flags_inverted_prohibition
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
