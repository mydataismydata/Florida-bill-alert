"""The email payloads.

What matters here is what a subscriber can and cannot end up receiving: an
unverified summary, an empty digest, or an area that no bill will ever match.
"""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DB = ROOT / "data" / "index.sqlite"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="no ingested corpus")


@pytest.fixture(scope="module")
def db():
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    return con


def test_an_empty_window_writes_nothing(db, tmp_path):
    """A newsletter that arrives to report nothing teaches people to ignore it."""
    from flba.digest import build
    assert build(db, "2026", tmp_path, "daily",
                 today=date(2020, 1, 1), days=1) is None
    assert not list(tmp_path.glob("*.json"))


def test_a_payload_names_bills_and_nothing_more(db, tmp_path):
    from flba.digest import build
    doc = build(db, "2026", tmp_path, "weekly", today=date(2025, 10, 1), days=30)
    assert doc and doc["bills"]
    allowed = {"num", "label", "title", "area", "area_slug", "one_line"}
    for b in doc["bills"]:
        assert set(b) == allowed, set(b) - allowed
        assert isinstance(b["num"], int)


def test_every_area_slug_is_one_the_form_offers(db, tmp_path):
    """A subscriber picks from thirteen. A payload carrying anything else is a
    bill no daily alert can ever match."""
    from flba.areas import AREAS, slug
    from flba.digest import build
    doc = build(db, "2026", tmp_path, "weekly", today=date(2025, 10, 1), days=30)
    valid = {slug(a) for a in AREAS} | {""}      # "" is a bill with no area
    assert {b["area_slug"] for b in doc["bills"]} <= valid


def test_only_a_verified_summary_travels(db, tmp_path):
    """The one_line in an email is the one the verifier passed, or nothing."""
    from flba.digest import build
    doc = build(db, "2026", tmp_path, "weekly", today=date(2025, 10, 1), days=30)
    for b in doc["bills"]:
        if not b["one_line"]:
            continue
        row = db.execute("SELECT one_line FROM analysis_ai WHERE session='2026' AND num=?",
                         (b["num"],)).fetchone()
        assert row and row["one_line"] == b["one_line"]


def test_the_payload_is_plain_data(db, tmp_path):
    """send.php assembles the body from these fields, so nothing here should
    carry markup of its own."""
    from flba.digest import build
    build(db, "2026", tmp_path, "weekly", today=date(2025, 10, 1), days=30)
    raw = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")
    doc = json.loads(raw)
    for b in doc["bills"]:
        for v in b.values():
            assert "<" not in str(v) and "\n" not in str(v), b
