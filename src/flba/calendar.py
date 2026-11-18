"""The session's own calendar, read out of the record.

The design's calendar carries dates, and the handoff says its dates are
placeholders to be checked against the official one. Rather than transcribe a
calendar from somewhere else and hope it stays true, these milestones are
derived from the 26,433 dated actions already in the index: the session
convened when bills were first introduced, it adjourned when the last one
moved, the Governor acted between the first and last approval.

That makes the calendar a reading of the record rather than a claim about it,
and it cannot drift out of date while the record is fresh. What it cannot give
is anything the record does not contain -- committee weeks that produced no
bill action, or next session's dates before next session exists.

Those come from ANNOUNCED instead: dates the Legislature has published ahead
of a session, each year with the document they were taken from. Once a
session's record exists, its own opening day replaces the announced one.
"""
from __future__ import annotations

import datetime
import re
from collections import Counter

_DATE = "%m/%d/%Y"
D = datetime.date

# (first day, last day or None, what, key). The key lets the record replace
# an announced date: "convene" gives way once bills have been introduced.
ANNOUNCED = {
    "2027": {
        "source": "https://www.flsenate.gov/PublishedContent/Offices/President/"
                  "2026-2027_Interim_Committee_Meeting_Schedule_.pdf",
        "source_label": "the Senate's interim schedule of August 3, 2026",
        "events": [
            (D(2026, 11, 17), None,
             "Organization session — the Legislature chooses its officers",
             "organize"),
            (D(2026, 11, 17), D(2026, 11, 19), "Training for senators", "training"),
            (D(2026, 11, 30), D(2026, 12, 3), "Committee week", "committee"),
            (D(2027, 1, 4), D(2027, 1, 7), "Committee week", "committee"),
            (D(2027, 1, 11), D(2027, 1, 14), "Committee week", "committee"),
            (D(2027, 2, 1), D(2027, 2, 4), "Committee week", "committee"),
            (D(2027, 2, 8), D(2027, 2, 11), "Committee week", "committee"),
            (D(2027, 2, 15), D(2027, 2, 18), "Committee week", "committee"),
            (D(2027, 3, 2), None, "Session convenes", "convene"),
            # Not in the memo. The Constitution limits a regular session to
            # 60 consecutive days (Art. III, s. 3(d)), counted from opening day.
            (D(2027, 4, 30), None, "Day 60 — the session's last scheduled day",
             "day60"),
        ],
        "note": "Day 60 follows from the Constitution's limit of 60 days "
                "on a regular session.",
    },
}


def announced(year: str, m: dict | None = None) -> list[dict]:
    """The dates announced for a year, less any its record now gives."""
    out = []
    for first, last, what, key in ANNOUNCED.get(year, {}).get("events", []):
        if key == "convene" and m and m.get("introduced"):
            continue
        out.append({"first": first, "last": last, "label": what})
    return out


def _announced_day(year: str, key: str) -> datetime.date | None:
    return next((first for first, _, _, k in
                 ANNOUNCED.get(year, {}).get("events", []) if k == key), None)


def status(year: str, today: datetime.date, m: dict | None) -> str:
    """UPCOMING before opening day, OPEN until the Governor is done, then
    CLOSED. The record decides where it has the dates, the announced
    schedule where it does not."""
    intro = m.get("introduced") if m else None
    convene = intro[0] if intro else _announced_day(year, "convene")
    if convene and today < convene:
        return "UPCOMING"
    if intro:
        return "CLOSED" if phase(today, m)[1] == "recess" else "OPEN"
    day60 = _announced_day(year, "day60")
    return "OPEN" if day60 and today <= day60 else "CLOSED"


def parse(text: str) -> datetime.date | None:
    try:
        return datetime.datetime.strptime((text or "").strip(), _DATE).date()
    except (ValueError, AttributeError):
        return None


def _span(rows, pattern):
    hits = [d for d, a in rows if re.search(pattern, a, re.I)]
    return (min(hits), max(hits), len(hits)) if hits else None


def milestones(history_rows, effective_dates) -> dict:
    """history_rows: (date string, action). effective_dates: date strings."""
    rows = [(parse(d), a) for d, a in history_rows]
    rows = [(d, a) for d, a in rows if d]
    if not rows:
        return {"events": [], "effective": [], "phase": "UNKNOWN"}

    filed = _span(rows, r"^\s*Filed")
    intro = _span(rows, r"Introduced")
    gov = _span(rows, r"Approved by Governor")
    veto = _span(rows, r"Vetoed")

    events = []
    if filed:
        events.append((filed[0], f"Filing opens — first of {filed[2]:,} bills filed"))
    if intro:
        events.append((intro[0], "Session convenes — bills introduced"))
        events.append((intro[1], "Last bill introduced"))
    if gov:
        events.append((gov[0], "Governor begins acting on bills"))
        events.append((gov[1], f"Governor's last action — {gov[2]} signed"))
    if veto and veto[2]:
        events.append((veto[1], f"{veto[2]} vetoed"))
    events.sort()

    # Where the laws actually land. Florida bunches effective dates on July 1
    # and October 1, and the design gives those rows their own line.
    counts = Counter()
    for text in effective_dates:
        d = parse(text)
        if d:
            counts[d] += 1
    effective = [{"date": d, "n": n, "big": n >= 10}
                 for d, n in sorted(counts.items()) if n >= 3]
    return {"events": events, "effective": effective,
            "filed": filed, "introduced": intro, "governor": gov}


def phase(today: datetime.date, m: dict) -> tuple[str, str]:
    """The stamp in the corner: what the Legislature is doing right now."""
    intro, gov = m.get("introduced"), m.get("governor")
    if intro and intro[0] <= today <= intro[1]:
        return "IN SESSION", "in session"
    if gov and intro and intro[1] < today <= gov[1]:
        return "GOVERNOR ACTION", "governor"
    return "IN RECESS", "recess"
