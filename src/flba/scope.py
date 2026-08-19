"""Who a bill actually applies to.

Florida bills routinely describe their reach by population band rather than by
name -- "counties with a population of 1.75 million or less" -- which is
precise, lawful, and unreadable. Resolving the band against the official
population series turns it into a list of counties a reader can check against
the place they live.

That resolution is arithmetic, not interpretation: the threshold is in the
bill, the populations are the series the Legislature itself uses, and the
answer is the same for anyone who does the sum.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data" / "fl_county_population.json"


def _load():
    raw = json.loads(_DATA.read_text())
    return raw, raw["counties"]


POP_DATA, COUNTY_POP = _load()
COUNTY_NAMES = sorted(COUNTY_POP, key=len, reverse=True)

# "1.75 million", "175,000", "1,750,000"
_NUM = r"(\d[\d,]*(?:\.\d+)?)\s*(million|thousand)?"

# Bands, in the phrasings the statutes actually use.
AT_MOST = re.compile(
    rf"population\s+of\s+(?:not\s+more\s+than\s+|no\s+more\s+than\s+|)"
    rf"{_NUM}\s*(?:or\s+(?:less|fewer)|or\s+below)", re.I)
AT_MOST_2 = re.compile(
    rf"population\s+of\s+(?:less\s+than|fewer\s+than|under)\s+{_NUM}", re.I)
AT_LEAST = re.compile(
    rf"population\s+of\s+(?:at\s+least|more\s+than|greater\s+than|over|"
    rf"exceeding)\s+{_NUM}", re.I)
BETWEEN = re.compile(
    rf"population\s+of\s+(?:between\s+)?{_NUM}\s*(?:to|through|and|-)\s*{_NUM}",
    re.I)

NAMED_COUNTY = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in COUNTY_NAMES) + r")\s+Count(?:y|ies)\b")

# Categories with a statutory meaning that a population number cannot express.
#
# The phrase appearing is not enough. SB 686 names "areas of critical state
# concern" in order to EXCLUDE that land, and reporting it as a limit on the
# bill's reach inverts the meaning. So a category counts only when the same
# sentence also carries limiting language -- a wrong claim in an "applies to"
# box is worse than a missing one.
CATEGORIES = {
    r"fiscally constrained": "fiscally constrained counties",
    r"rural areas? of opportunity": "rural areas of opportunity",
    r"charter count(?:y|ies)": "charter counties",
    r"areas? of critical state concern": "areas of critical state concern",
}
LIMITING = re.compile(
    r"\b(?:only|solely|limited to|applies only|shall apply only|exclusively|"
    r"restricted to)\b", re.I)


def _limited_categories(text: str) -> list[str]:
    found = set()
    for sentence in re.split(r"(?<=[.;])\s+", text):
        if not LIMITING.search(sentence):
            continue
        for pattern, label in CATEGORIES.items():
            if re.search(pattern, sentence, re.I):
                found.add(label)
    return sorted(found)


def _value(num: str, unit: str | None) -> int:
    n = float(num.replace(",", ""))
    if unit:
        n *= 1_000_000 if unit.lower() == "million" else 1_000
    return int(round(n))


@dataclass
class Scope:
    kind: str                       # statewide | counties | categories
    counties: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    thresholds: list[str] = field(default_factory=list)
    named: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)

    @property
    def statewide(self) -> bool:
        return self.kind == "statewide"

    @property
    def describe(self) -> str:
        if self.statewide:
            return "Applies statewide — all 67 counties."
        if self.named and not self.thresholds:
            return f"Named in the bill: {', '.join(self.named)}."
        if self.categories and not self.counties:
            # A statutory category is a defined list that changes over time,
            # so naming the category is honest where guessing a list is not.
            return ("Limited to " + ", ".join(self.categories)
                    + " — a statutory category, not a fixed list of counties.")
        n = len(self.counties)
        if self.excluded and n:
            return (f"{n} of 67 counties — every county except "
                    f"{', '.join(self.excluded)}.")
        return f"{n} of 67 counties." if n else "Scope not stated numerically."

    def as_dict(self) -> dict:
        return {**self.__dict__, "describe": self.describe,
                "statewide": self.statewide}


def population_bands(text: str) -> list[tuple[int | None, int | None, str]]:
    """Extract (low, high, phrase) population bands stated in the text."""
    out = []
    for m in BETWEEN.finditer(text):
        lo = _value(m.group(1), m.group(2))
        hi = _value(m.group(3), m.group(4))
        out.append((min(lo, hi), max(lo, hi), m.group(0).strip()))
    for rx, shape in ((AT_MOST, "max"), (AT_MOST_2, "max"), (AT_LEAST, "min")):
        for m in rx.finditer(text):
            v = _value(m.group(1), m.group(2))
            out.append((None, v, m.group(0).strip()) if shape == "max"
                       else (v, None, m.group(0).strip()))
    seen, uniq = set(), []
    for band in out:
        key = (band[0], band[1])
        if key not in seen:
            seen.add(key)
            uniq.append(band)
    return uniq


def resolve(text: str) -> Scope:
    """Work out which counties a bill's own words reach."""
    bands = population_bands(text)
    named = sorted({m.group(1) for m in NAMED_COUNTY.finditer(text)
                    if m.group(1) in COUNTY_POP})
    cats = _limited_categories(text)

    if not bands:
        if named:
            return Scope(kind="counties", counties=named, named=named,
                         categories=cats)
        return Scope(kind="statewide" if not cats else "categories",
                     counties=[] if cats else sorted(COUNTY_POP),
                     categories=cats)

    inside = set(COUNTY_POP)
    phrases = []
    for lo, hi, phrase in bands:
        phrases.append(phrase)
        inside &= {c for c, p in COUNTY_POP.items()
                   if (lo is None or p >= lo) and (hi is None or p <= hi)}
    outside = sorted(set(COUNTY_POP) - inside)
    return Scope(kind="counties", counties=sorted(inside),
                 excluded=outside if len(outside) <= 8 else [],
                 thresholds=phrases, named=named, categories=cats)


# --- who filed it -----------------------------------------------------------

_SENATE = Path(__file__).resolve().parent / "data" / "fl_senate_districts.json"
try:
    SENATE_MEMBERS = json.loads(_SENATE.read_text())["members"]
except FileNotFoundError:            # the roster is optional
    SENATE_MEMBERS = {}


def sponsor_districts(sponsor: str, chamber: str) -> list[dict]:
    """Match a bill's sponsors to their districts.

    Only the Senate is covered. The Senate publishes a roster carrying each
    district's county composition; the House does not expose an equivalent on
    this site, so House sponsors resolve to nothing rather than to a guess.
    """
    if chamber != "Senate" or not sponsor:
        return []
    found = []
    for part in re.split(r"[;,]", sponsor):
        last = part.strip().split()[-1] if part.strip() else ""
        member = SENATE_MEMBERS.get(last)
        if member and member not in found:
            found.append(member)
    return found
