"""What goes in an email, decided here rather than on the public server.

The sending side addresses and delivers; it never composes. Everything a
subscriber reads is written on this machine, from the same database the site is
built from, and shipped as a payload in the same bundle as the HTML. That keeps
the composing side private and leaves the public server holding files it can
only hand out.

A payload names the bills and nothing more -- label, title, area, and the
one-line summary if one has been verified. The body of the mail is assembled
from those fields by send.php, so a payload cannot smuggle markup into an
email.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from .areas import classify, slug


def _rows(db, sql, *args):
    return db.execute(sql, args).fetchall()


def recent(db, session: str, since: date, until: date) -> list[dict]:
    """Bills whose history shows their first action inside the window.

    Filing is the event a subscriber cares about for the daily alert -- it is
    the moment a bill exists -- and it is the one date every bill has.
    """
    seen: dict[int, str] = {}
    for r in _rows(db, "SELECT num, date FROM history WHERE session=? AND date<>''",
                   session):
        try:
            m, d, y = (int(x) for x in r["date"].split("/"))
        except (ValueError, AttributeError):
            continue
        when = date(y, m, d).isoformat()
        if r["num"] not in seen or when < seen[r["num"]]:
            seen[r["num"]] = when

    lo, hi = since.isoformat(), until.isoformat()
    return [num for num, when in sorted(seen.items()) if lo <= when <= hi]


def payload(db, session: str, product: str, nums: list[int],
            cap: int = 40) -> dict:
    """One email's worth of bills, as data."""
    refs: dict[int, list] = {}
    for r in _rows(db, "SELECT num, statute FROM statute_ref WHERE session=?", session):
        refs.setdefault(r["num"], []).append(r["statute"])

    bills = []
    for num in nums[:cap]:
        b = db.execute("SELECT * FROM bill WHERE session=? AND num=?",
                       (session, num)).fetchone()
        if b is None:
            continue
        area = classify(refs.get(num, []), b["title"] or "")
        ai = db.execute("SELECT one_line FROM analysis_ai WHERE session=? AND num=?",
                        (session, num)).fetchone()
        bills.append({
            "num": b["num"],
            "label": b["label"],
            "title": b["title"] or "",
            "area": area or "General",
            "area_slug": slug(area),
            # Only a summary that survived verification. No analysis means no
            # line, never a guess to fill the space.
            "one_line": (ai["one_line"] if ai and ai["one_line"] else ""),
        })
    return {"product": product, "session": session,
            "generated": date.today().isoformat(), "bills": bills}


def build(db, session: str, out: Path, product: str, today: date | None = None,
          days: int | None = None) -> dict | None:
    """Write one payload, or return None when there is nothing to say.

    An empty digest is not sent. A newsletter that arrives to report that
    nothing happened teaches people to ignore it.
    """
    today = today or date.today()
    span = days or (1 if product == "daily" else 7)
    nums = recent(db, session, today - timedelta(days=span), today)
    if not nums:
        return None

    doc = payload(db, session, product, nums)
    if not doc["bills"]:
        return None

    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{today.isoformat()}-{product}.json"
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    doc["path"] = str(path)
    return doc
