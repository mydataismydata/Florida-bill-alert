"""Analysis requests from the public site.

What matters is who ends up being told a bill is ready, and when: only once
the page they will open carries the analysis, never for a request the
payload did not see, and never in a form an older sender would mail to every
subscriber.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from flba import requested  # noqa: E402
from flba.store import Store  # noqa: E402


@pytest.fixture
def db(tmp_path):
    db = Store(tmp_path / "t.sqlite").db
    for session, num, label in (("2026", 4, "SB 4"), ("2026", 9, "SB 9"),
                                ("2027", 12, "SB 12"), ("2027", 14, "SB 14")):
        db.execute("INSERT INTO bill (session, num, label, title)"
                   " VALUES (?,?,?,?)", (session, num, label, f"Title {num}"))
    db.execute("INSERT INTO analysis_ai (session, num, one_line)"
               " VALUES ('2027', 12, 'Does a thing.')")
    return db


def req(session, num, first="2026-11-19 10:00:00", last=None, n=1):
    return {"session": session, "num": num, "first": first,
            "last": last or first, "n": n}


def page(site, path, analysed):
    p = site / path
    p.parent.mkdir(parents=True, exist_ok=True)
    form = "" if analysed else '<form action="../request-analysis.php">'
    p.write_text(f"<html>{form}</html>", encoding="utf-8")


def test_parse_keeps_only_entries_that_name_a_bill():
    out = json.dumps([
        {"session": "2026", "num": 4, "first": "a", "last": "b", "n": 2},
        {"session": "2026A", "num": "7", "first": "a", "last": "a", "n": 1},
        {"session": "../x", "num": 4},
        {"session": "2026", "num": 0},
        {"session": "2026", "num": "x"},
        "junk",
    ])
    rows = requested.parse("PHP Notice: something\n" + out + "\n")
    assert [(r["session"], r["num"]) for r in rows] == [("2026", 4), ("2026A", 7)]


def test_parse_refuses_output_that_is_not_the_list():
    with pytest.raises(requested.Unavailable):
        requested.parse("")
    with pytest.raises(requested.Unavailable):
        requested.parse("Not configured.")


def test_queue_skips_unknown_and_analysed_bills(db):
    reqs = [req("2026", 4), req("2027", 12), req("2026", 555)]
    assert [(r["session"], r["num"]) for r in requested.queue(db, reqs)] \
        == [("2026", 4)]


def test_a_bill_that_keeps_failing_falls_behind_the_rest(db):
    reqs = [req("2026", 4, first="2026-11-01 00:00:00"),
            req("2026", 9, first="2026-11-18 00:00:00"),
            req("2027", 14, first="2026-11-19 00:00:00")]
    requested.record_try(db, "2026", 4)
    order = [(r["session"], r["num"]) for r in requested.queue(db, reqs)]
    assert order == [("2026", 9), ("2027", 14), ("2026", 4)]
    assert len(requested.queue(db, reqs, limit=2)) == 2


def test_ready_only_once_the_published_page_drops_the_button(db, tmp_path):
    site = tmp_path / "site"
    page(site, "bills/4.html", analysed=False)          # not done yet
    page(site, "bills/2027/12.html", analysed=True)     # done
    reqs = [req("2026", 4), req("2027", 12, last="2026-11-19 11:00:00"),
            req("2027", 14)]                            # 14: no page at all
    doc = requested.answer(db, reqs, site)
    assert [(b["session"], b["num"]) for b in doc["ready"]] == [("2027", 12)]
    b = doc["ready"][0]
    assert b["path"] == "bills/2027/12.html"
    assert b["one_line"] == "Does a thing."
    assert b["upto"] == "2026-11-19 11:00:00"
    assert doc["unknown"] == []


def test_a_request_for_no_bill_is_closed_not_answered(db, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    doc = requested.answer(db, [req("2026", 555)], site)
    assert doc["ready"] == []
    assert doc["unknown"] == [{"session": "2026", "num": 555,
                               "upto": "2026-11-19 10:00:00"}]


def test_nothing_to_say_writes_nothing(db, tmp_path):
    site = tmp_path / "site"
    page(site, "bills/4.html", analysed=False)
    assert requested.answer(db, [req("2026", 4)], site) is None
    assert requested.answer(db, [], site) is None


def test_the_payload_can_never_be_mailed_as_a_digest(db, tmp_path):
    """send.php mails any payload with a `bills` list to subscribers, and one
    that predates the ready kind reads anything not daily as weekly."""
    site = tmp_path / "site"
    page(site, "bills/2027/12.html", analysed=True)
    doc = requested.answer(db, [req("2027", 12)], site)
    assert doc["product"] == "ready"
    assert "bills" not in doc
    path = requested.write(doc, tmp_path / "mail", "2026-11-20")
    assert path.name == "2026-11-20-ready.json"
    assert json.loads(path.read_text())["ready"][0]["num"] == 12


def test_the_payload_carries_no_address(db, tmp_path):
    site = tmp_path / "site"
    page(site, "bills/2027/12.html", analysed=True)
    doc = requested.answer(db, [req("2027", 12)], site)
    assert "@" not in json.dumps(doc)
