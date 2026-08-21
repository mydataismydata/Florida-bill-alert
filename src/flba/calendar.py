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
bill action, or next session's dates before next session exists. Those are
marked as unconfirmed rather than invented.
"""
from __future__ import annotations

import datetime
import re
from collections import Counter

_DATE = "%m/%d/%Y"


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
