"""Parser for the Florida House member roster.

The Senate site carries bills for both chambers but names House sponsors by
surname alone. The House publishes its own roster, and that is the only place
a House surname can be turned back into a person.

Markup notes (verified 2026-08): `myfloridahouse.gov` redirects to
`flhouse.gov`, which serves the roster as plain server-rendered HTML -- one
`div.team-box` per member, holding a `MemberId` link, an `<h5>` with the name
in `Last, First Middle "Nickname"` form, and a paragraph carrying party and
district. The site sits behind a firewall that rejects a request carrying a
bare tool User-Agent; the crawler's own identifying string is accepted.

The roster is a snapshot of who sits today. Seven districts carry two entries
in 2026 because a member left mid-term and was replaced, and both may have
sponsored bills, so districts are not unique and must not be a primary key.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

BASE = "https://www.flhouse.gov"
ROSTER = f"{BASE}/Representatives"

_MEMBER_ID = re.compile(r"MemberId=(\d+)")
_DISTRICT = re.compile(r"District:\s*(\d+)", re.I)
_PARTY = re.compile(r"\b(Republican|Democrat)\b", re.I)


def _txt(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def parse_roster(html: str) -> list[dict]:
    """Every sitting representative: name, party, district, member id."""
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()

    for box in soup.select("div.team-box"):
        link = box.select_one("a[href*='MemberId=']")
        if not link:
            continue
        mid = _MEMBER_ID.search(link["href"])
        name = _txt(box.select_one("h5"))
        blurb = " ".join(_txt(p) for p in box.select("p"))
        district = _DISTRICT.search(blurb)
        party = _PARTY.search(blurb)
        if not (mid and name and district):
            continue
        key = mid.group(1)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "member_id": int(key),
            "name": name,
            "district": int(district.group(1)),
            "party": party.group(1).title() if party else "",
            "url": f"{BASE}/Sections/Representatives/details.aspx?MemberId={key}",
        })
    return out
