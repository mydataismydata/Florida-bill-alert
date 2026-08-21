"""Which policy area a bill belongs to.

The design leans on thirteen fixed areas -- for the chips on the front page,
for filtering, and for what a subscriber asks to hear about. Nothing in the
record names them: the Legislature's own `subject` field is the bill's type
("GENERAL BILL", "JOINT RESOLUTION"), not its topic.

So they are derived the way everything else here is derived -- from the
statutes the bill actually changes. Florida Statutes are organised by chapter,
and the chapters are already topical: 1002 is education, 627 insurance, 316
traffic, 163 land use. A bill that amends them is about those things, and the
cross-reference index already knows which it amends. No model is involved and
no keyword guessing either, which matters because this decides what lands in
somebody's inbox.

A bill with no statute references -- a resolution, a new act that creates
rather than amends -- falls back to its title, and then to nothing. Better an
unfiled bill than a miscategorised one.
"""
from __future__ import annotations

import re

# The thirteen, fixed. A subscriber's stored interests reference these, so the
# list is an enum and not free text: renaming one silently re-points every
# subscription that used it.
AREAS = [
    "Agriculture",
    "AI & Technology",
    "Criminal Justice",
    "Development & Land Use",
    "Education",
    "Elections",
    "Environment & Water",
    "Healthcare",
    "Housing",
    "Insurance",
    "Local Government",
    "Taxes & Budget",
    "Transportation",
]

# Chapter ranges, from the Florida Statutes' own table of contents. Ranges are
# inclusive and tried in order, so a narrower rule below beats a wider one
# above it.
_CHAPTERS: list[tuple[str, list[tuple[int, int]]]] = [
    ("Elections",              [(97, 107)]),
    ("Education",              [(1000, 1013), (228, 246)]),
    ("Criminal Justice",       [(775, 985), (741, 741), (943, 947)]),
    ("Transportation",         [(316, 349), (310, 315)]),
    ("Environment & Water",    [(253, 259), (369, 380), (403, 403),
                                (373, 373), (161, 162)]),
    ("Healthcare",             [(381, 408), (456, 499), (409, 409),
                                (394, 397)]),
    ("Insurance",              [(624, 651)]),
    ("Agriculture",            [(500, 604)]),
    ("Housing",                [(420, 424), (718, 723)]),
    ("Development & Land Use", [(163, 164), (177, 177), (190, 190),
                                (333, 333), (553, 553)]),
    ("Local Government",       [(125, 125), (165, 189), (112, 112),
                                (119, 119), (286, 286)]),
    ("Taxes & Budget",         [(192, 220), (73, 76), (215, 218)]),
]

# Only where the statutes cannot say. "Artificial intelligence" has no chapter
# of its own -- it turns up inside education, procurement and criminal law --
# so it is the one area that has to be read off the words.
_TITLE_RULES: list[tuple[str, re.Pattern]] = [
    ("AI & Technology", re.compile(
        r"\b(artificial intelligence|machine learning|algorithm\w*|"
        r"autonomous|deepfake|generative|chatbot|social media platform|"
        r"data centers?|cryptocurrenc\w+|digital asset\w*|blockchain)\b", re.I)),
    ("Elections",       re.compile(r"\b(election|ballot|votin|voter|redistrict)\w*\b", re.I)),
    ("Education",       re.compile(r"\b(school|student|charter school|university|"
                                   r"college|teacher|K-12)\b", re.I)),
    ("Criminal Justice", re.compile(r"\b(criminal|felony|misdemeanor|sentenc\w+|"
                                    r"offender|inmate|probation|law enforcement)\b", re.I)),
    ("Healthcare",      re.compile(r"\b(health|medicaid|medicare|hospital|patient|"
                                   r"nurs\w+|physician|prescription|mental health)\b", re.I)),
    ("Transportation",  re.compile(r"\b(transportation|highway|motor vehicle|"
                                   r"driver licen\w+|airport|seaport|toll)\b", re.I)),
    ("Environment & Water", re.compile(r"\b(environment\w*|water|wetland|coastal|"
                                       r"pollut\w+|conservation|everglades)\b", re.I)),
    ("Housing",         re.compile(r"\b(housing|landlord|tenant|condominium|"
                                   r"homeowners association|eviction)\b", re.I)),
    ("Insurance",       re.compile(r"\b(insur\w+|reinsur\w+|underwrit\w+)\b", re.I)),
    ("Agriculture",     re.compile(r"\b(agricultur\w+|farm\w*|livestock|citrus|"
                                   r"aquacultur\w+|forestry)\b", re.I)),
    ("Taxes & Budget",  re.compile(r"\b(tax\w*|appropriation|revenue|millage|"
                                   r"trust fund|budget)\b", re.I)),
    ("Local Government", re.compile(r"\b(municipalit\w+|count(y|ies)|special district|"
                                    r"public records|public officers?)\b", re.I)),
    ("Development & Land Use", re.compile(r"\b(land use|comprehensive plan|zoning|"
                                          r"development|building code|permitting)\b", re.I)),
]

_CHAPTER_OF = re.compile(r"^(\d+)")


def chapter_of(statute: str) -> int | None:
    m = _CHAPTER_OF.match((statute or "").strip())
    return int(m.group(1)) if m else None


def area_of_chapter(chapter: int | None) -> str | None:
    if chapter is None:
        return None
    for area, ranges in _CHAPTERS:
        for lo, hi in ranges:
            if lo <= chapter <= hi:
                return area
    return None


def classify(statutes, title: str = "") -> str | None:
    """The area a bill belongs to, or None if nothing says.

    Statutes decide it. Where a bill touches several areas, the one it changes
    in the most places wins -- an education bill that also amends a records
    chapter is still an education bill.
    """
    counts: dict[str, int] = {}
    for cite in statutes or ():
        area = area_of_chapter(chapter_of(cite))
        if area:
            counts[area] = counts.get(area, 0) + 1

    # Technology cuts across chapters, so it is read from the title even when
    # the statutes have already spoken.
    tech = _TITLE_RULES[0]
    if title and tech[1].search(title):
        return tech[0]

    if counts:
        return max(sorted(counts), key=lambda a: counts[a])
    for area, pattern in _TITLE_RULES:
        if title and pattern.search(title):
            return area
    return None


def slug(area: str | None) -> str:
    """URL form of an area name: 'AI & Technology' -> 'ai-technology'."""
    if not area:
        return ""
    out = re.sub(r"[^a-z0-9]+", "-", area.lower()).strip("-")
    return out
