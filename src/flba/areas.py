"""Which policy area a bill belongs to.

The design leans on sixteen fixed areas -- for the chips on the front page,
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

# The sixteen, fixed. A subscriber's stored interests reference these, so the
# list is an enum and not free text: renaming one silently re-points every
# subscription that used it. public/lib.php holds the same list in slug form
# and the two must not drift.
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
    "Legal",
    "Local Government",
    "Occupational",
    "Public Health & Safety",
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
    # 479 is outdoor advertising -- billboard permits along the state highway
    # system. It sits in the professions title by accident of codification;
    # the subject is roads.
    ("Transportation",         [(316, 349), (310, 315), (479, 479)]),
    ("Environment & Water",    [(253, 259), (369, 380), (403, 403),
                                (373, 373), (161, 162)]),
    # Title XXIX is "Public Health", which is wider than health care. These
    # chapters are the public-health-and-safety end of it: mosquito control,
    # elevator safety, the department's miscellaneous programmes, radiation,
    # medical records held for research, and medical examiners. Listed above
    # Healthcare so the broad (381, 408) below picks up only the rest.
    # 403 -- environmental control -- is claimed by Environment & Water
    # further up, which is where a reader looks for permitting and pollution.
    ("Public Health & Safety", [(388, 388), (399, 399), (402, 402),
                                (404, 406)]),
    # Title XXXII is "Regulation of Professions and Occupations": chapters 454
    # to 493, not 499. The health boards are 456-467, plus veterinary (474),
    # electrolysis (478), massage (480), laboratory and therapy practice
    # (483-486), and psychology and counselling (490-491). 468 is a catch-all
    # chapter and is split by part below rather than by number. 429 is assisted
    # living, which is Title XXX and so was never in the old range at all.
    # 499 is the Florida Drug and Cosmetic Act.
    ("Healthcare",             [(381, 408), (456, 468), (474, 474),
                                (478, 478), (480, 480), (483, 486),
                                (490, 491), (499, 499),
                                (409, 409), (429, 429), (394, 397)]),
    # Everything else that is a licence to practise a trade. 455 is the
    # department's own general provisions, 470 and 497 are funeral and
    # cemetery services, and the rest run from asbestos abatement through
    # engineering, accountancy, real estate, cosmetology, pest control,
    # contracting, geology and private security.
    ("Occupational",           [(455, 455), (469, 473), (475, 477),
                                (481, 482), (487, 489), (492, 493),
                                (497, 497)]),
    # Lawyers and the machinery they work in: attorneys (454), state
    # attorneys and public defenders (27, 29), the courts and mediation
    # (43-45), venue and process (47, 48), legal advertisements (50), costs
    # and fees (57), miscellaneous civil proceedings (69), evidence (90),
    # limitations (95), probate and trusts (733, 736), guardianship (744)
    # and negligence (768). A statute about a specific subject another area
    # already holds stays with that area -- this is the general law of
    # practice, not every law a lawyer reads.
    ("Legal",                  [(27, 27), (29, 29), (43, 45), (47, 48),
                                (50, 50), (57, 57), (69, 69), (90, 90),
                                (95, 95), (454, 454), (733, 733),
                                (736, 736), (744, 744), (768, 768)]),
    ("Insurance",              [(624, 651)]),
    # (500, 604) is Florida's "Regulation of Trade, Commerce, Investments,
    # and Solicitations" title -- chapter 500 (food products) and 570-604
    # (Dept. of Agriculture and Consumer Services, plant and animal industry,
    # aquaculture) are the department's real turf, but the same span also
    # holds gambling (546, 550-551), pawnbrokers (538-539), securities and
    # money transmitters (517, 520, 560), alcohol and tobacco (561-569),
    # building codes (553), and hotels/vacation rentals (509) -- none of it
    # agriculture. Keep only the confirmed chapters rather than the whole
    # title.
    ("Agriculture",            [(500, 500), (570, 604)]),
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
        # "Autonomous" on its own is a licensure word before it is a technical
        # one -- "Autonomous Practice" is a nurse's scope, not a machine's --
        # so it only counts when it is driving something.
        r"autonomous (?=vehicle|vessel|aircraft|drone|robot|system|"
        r"technolog|deliver|truck)|"
        r"deepfake|generative|chatbot|social media platform|"
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

# Chapter 468 is itself a catch-all -- "Miscellaneous Professions and
# Occupations" -- so genuine health practices (respiratory care, dietetics,
# music therapy) sit interleaved with trades that aren't health at all.
# Section numbers here are identifiers, not decimals (468.8322 is not "more"
# than 468.84), so these are matched as string prefixes, not numeric ranges.
# Confirmed non-health parts: .43 community association management, .60-.63
# building code administrators and inspectors, .83 home inspection services.
# A couple of other parts in this chapter (roughly .38-.41, .84) are cited
# only by omnibus bills that touch nearly every board at once, so there's no
# clean bill to confirm what they are -- left as Healthcare rather than
# guessed at.
_OCCUPATIONAL_468_PREFIXES = ("468.43", "468.60", "468.61", "468.62",
                              "468.63", "468.83")


def chapter_of(statute: str) -> int | None:
    m = _CHAPTER_OF.match((statute or "").strip())
    return int(m.group(1)) if m else None


def area_of_statute(statute: str) -> str | None:
    s = (statute or "").strip()
    if s.startswith(_OCCUPATIONAL_468_PREFIXES):
        return "Occupational"
    return area_of_chapter(chapter_of(s))


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
        area = area_of_statute(cite)
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
