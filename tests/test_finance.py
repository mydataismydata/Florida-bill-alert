"""Matching a sponsor to their campaign filings.

The failure this guards against is silent: link "Rep. Smith" to the wrong
Smith and the page looks exactly as correct as it would if the money were
theirs. So the tests care less about how many sponsors get a link than about
which ones are refused one.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from flba.finance import (Tracker, display_name, fold, match,  # noqa: E402
                          parse_name, roster_index, sponsor_names, squash,
                          split_token, surname_forms)

DB = ROOT / "data" / "index.sqlite"


# Every one of these is a real 2026 roster entry, and each broke an earlier
# version of the parser.
@pytest.mark.parametrize("raw,surname,givens", [
    ('Mooney Jr., James Vernon "Jim"', "Mooney", ["James", "Jim"]),
    ('Robinson Jr., William Cloud "Will"', "Robinson", ["William", "Will"]),
    ("Franklin II, Gallop", "Franklin", ["Gallop"]),
    ("Eskamani, Dr. Anna  V.", "Eskamani", ["Anna"]),
    ("Abbott, Shane G.", "Abbott", ["Shane"]),
    ("Gossett-Seidman, Peggy", "Gossett-Seidman", ["Peggy"]),
    ('Andrade, Robert Alexander "Alex"', "Andrade", ["Robert", "Alex"]),
    # The Senate writes the name the other way round, and its comma separates
    # a suffix rather than a given name.
    ("Don Gaetz", "Gaetz", ["Don"]),
    ("Ralph E. Massullo, Jr.", "Massullo", ["Ralph"]),
    ("Jason W. B. Pizzo", "Pizzo", ["Jason"]),
])
def test_parse_name(raw, surname, givens):
    assert parse_name(raw) == (surname, givens)


def test_nickname_is_kept_as_an_alternative_not_a_replacement():
    """Andrade filed as Alex and sits as Robert; only trying both finds him."""
    _, givens = parse_name('Andrade, Robert Alexander "Alex"')
    assert givens == ["Robert", "Alex"]


def test_a_senate_nickname_survives_into_the_search():
    """Tom Leek's filings are under Tom; the Senate lists him as Thomas J.

    The chamber publishes both and the page shows the formal one, so the
    nickname has to reach the lookup by some other route or the link is lost.
    """
    surname, givens = parse_name('Thomas J. Leek "Tom"')
    assert surname == "Leek"
    assert givens == ["Thomas", "Tom"]
    assert display_name('Thomas J. Leek "Tom"') == "Thomas J. Leek"


@pytest.mark.skipif(not DB.exists(), reason="no ingested corpus")
def test_the_senate_roster_keeps_the_nicknames_it_publishes():
    db = sqlite3.connect(str(DB))
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT name, nickname FROM member"
                      " WHERE chamber='Senate'").fetchall()
    if not rows:
        pytest.skip("run: flba members")
    assert any(r["nickname"] for r in rows), (
        "no nickname stored -- parse_senator_page is dropping them again")
    for r in rows:
        assert '"' not in r["name"], f"{r['name']} still carries its nickname"


def test_accents_are_folded_because_filings_are_ascii():
    assert fold("Valdés") == "Valdes"
    assert fold("Fabián") == "Fabian"
    assert "Valdes" in surname_forms("Valdés")


def test_hyphenated_surnames_offer_each_half():
    """Peggy Gossett-Seidman's filing answers to either half of her surname."""
    assert surname_forms("Gossett-Seidman") == [
        "Gossett-Seidman", "Gossett", "Seidman"]


def test_a_plain_surname_is_asked_about_once():
    assert surname_forms("Abbott") == ["Abbott"]


@pytest.mark.parametrize("raw,shown", [
    ('Mooney Jr., James Vernon "Jim"', "James Mooney"),
    ("LaMarca, Chip", "Chip LaMarca"),
    ("Eskamani, Dr. Anna  V.", "Anna Eskamani"),
    # Already in reading order: leave it alone rather than risk dropping a
    # word. PAC Tracker shortens this one to "Lavon Davis".
    ("LaVon Bracy Davis", "LaVon Bracy Davis"),
    ("Carlos Guillermo Smith", "Carlos Guillermo Smith"),
])
def test_display_name_keeps_the_chambers_spelling(raw, shown):
    assert display_name(raw) == shown


class FakeTracker(Tracker):
    """A Tracker answering from a fixture, so the prefix rules can be tested
    without reaching the network."""

    def __init__(self, filings):
        super().__init__(base="http://x", public="http://x")
        self.filings = filings
        self.asked = []

    def lookup(self, surname, given):
        self.asked.append((surname.lower(), given.lower()))
        parts = [p for last, first, p in self.filings
                 if last == surname.lower() and first.startswith(given.lower())]
        if not parts:
            return None
        return {"person": {"name": "x", "last": surname.lower(),
                           "first": given.lower(), "parts": parts,
                           "totalReceived": "1", "totalGiven": "0"}}


def test_a_name_with_punctuation_is_reached_by_prefix():
    """Ra'Shon is not reachable as RaShon, and a dot in a path is not routed.

    A prefix is, so that is the way in -- see the refusal test below for what
    stops it linking the wrong person.
    """
    t = FakeTracker([
        ("young", "ra'shon", {"kind": "candidate", "name": "Young, Ra'Shon  (DEM)(STR)"}),
        ("grow", "j.j.", {"kind": "candidate", "name": "Grow, J.J.  (REP)(STR)"}),
    ])
    assert t.by_prefix("Young", "RaShon")["url"].endswith("/young/ra")
    assert t.by_prefix("Grow", "J.J.")["url"].endswith("/grow/j")


def test_a_prefix_that_lands_on_someone_else_is_refused():
    """Asking PAC Tracker for Smith and 'a' returns Kathleen A. Smith, matched
    on her middle initial. The answer is checked, so this cannot become a
    link."""
    t = FakeTracker([
        ("smith", "kathleen a.", {"kind": "candidate",
                                  "name": "Smith, Kathleen A. (REP)(PUB)"}),
    ])
    assert t.by_prefix("Smith", "Aaron") is None
    assert t.by_prefix("Smith", "Alan") is None


def test_a_prefix_matching_two_people_is_refused():
    """One link cannot stand for two people, however alike their names."""
    t = FakeTracker([
        ("hall", "jonathan", {"kind": "candidate", "name": "Hall, Jonathan (REP)(STR)"}),
        ("hall", "jonquil", {"kind": "candidate", "name": "Hall, Jonquil (DEM)(STR)"}),
    ])
    assert t.by_prefix("Hall", "Jon") is None


def test_a_single_letter_name_is_never_guessed_at():
    t = FakeTracker([("x", "a", {"kind": "candidate", "name": "X, A (REP)(STR)"})])
    assert t.by_prefix("X", "A") is None
    assert not t.asked, "should not have asked at all"


def test_squash_ignores_only_punctuation_and_case():
    assert squash("Ra'Shon") == squash("RaShon") == "rashon"
    assert squash("J.J.") == squash("JJ") == "jj"
    assert squash("Valdés") == "valdes"
    assert squash("Anne") != squash("Ann")


def test_committees_are_not_people():
    """A bill through three committees names one person who has donors."""
    committees = {"Rules", "Judiciary", "Community Affairs"}
    assert sponsor_names("Rules; Judiciary; McClain", committees) == ["McClain"]
    assert sponsor_names("Criminal Justice Subcommittee; Baker") == ["Baker"]
    assert sponsor_names("Agriculture", {"Agriculture"}) == []


def test_the_houses_initial_disambiguates_a_shared_surname():
    roster = roster_index([
        {"name": 'Alvarez, Daniel Antonio "Danny"', "district": 69, "party": "R"},
        {"name": "Alvarez, Jose", "district": 46, "party": "D"},
    ])
    assert split_token("Alvarez, D.") == ("Alvarez", "D")
    assert match("Alvarez, D.", roster)["district"] == 69
    assert match("Alvarez, J.", roster)["district"] == 46


def test_a_shared_surname_without_an_initial_is_refused():
    """Two Alvarezes and nothing to tell them apart: show no link at all."""
    roster = roster_index([
        {"name": 'Alvarez, Daniel Antonio "Danny"', "district": 69, "party": "R"},
        {"name": "Alvarez, Jose", "district": 46, "party": "D"},
    ])
    assert match("Alvarez", roster) is None


def test_an_unknown_surname_is_refused():
    roster = roster_index([{"name": "Abbott, Shane G.", "district": 5,
                            "party": "R"}])
    assert match("Nobody", roster) is None


def test_a_sole_holder_of_a_surname_matches_without_an_initial():
    roster = roster_index([{"name": "Hunschofsky, Christine", "district": 96,
                            "party": "D"}])
    assert match("Hunschofsky", roster)["district"] == 96


@pytest.mark.skipif(not DB.exists(), reason="no ingested corpus")
def test_every_stored_link_names_a_person_and_a_public_url():
    """Nothing half-resolved reaches a page: a row either links or does not."""
    db = sqlite3.connect(str(DB))
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM sponsor_finance").fetchall()
    if not rows:
        pytest.skip("run: flba finance")
    for r in rows:
        if not r["url"]:
            continue
        assert r["member_name"], f"{r['token']} links with no name"
        assert r["url"].startswith("https://"), (
            f"{r['token']} would ship a non-public link: {r['url']}")
        assert "localhost" not in r["url"]
