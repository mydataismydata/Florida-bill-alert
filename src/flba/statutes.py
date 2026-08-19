"""Which statutes a bill touches, and what it does to each one.

Deterministic, like the rest of the Phase 1 layer. Florida bills are required
to say what they are doing in the formal title, and then to do it in numbered
sections whose openers follow a small set of forms:

    Section 1. Subsection (15) of section 215.422, Florida Statutes,
               is amended to read:
    Section 7. Section 497.1411, Florida Statutes, is created to read:
    Section 3. Subsection (16) is added to section 493.6102, Florida
               Statutes, to read:
    Section 9. Section 322.27, Florida Statutes, is repealed.

Pairing those with the additions/deletions from `flba.diff` yields the claim
this project is really for: *this bill amends s. 215.422, deleting 34 words and
adding 12, starting at line 118* -- every part of which a reader can check
against the bill in front of them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .diff import DELETE, INSERT, PLAIN, BillDiff, normalize

# The year of the statute edition the site links to.
STATUTE_YEAR = 2025

# "Section 12." opening a numbered section of the bill.
SECTION_MARK = re.compile(r"\bSection\s+(\d+)\.\s")

# The statute a section operates on. Bills say it two ways.
STATUTE_IN_SECTION = re.compile(
    r"\bsection\s+(\d+[0-9A-Za-z.]*)\s*,\s*Florida\s+Statutes", re.I)
STATUTE_SHORT = re.compile(r"\bs{1,2}\.\s*(\d+[0-9A-Za-z.]*)\s*,\s*F\.S\.", re.I)

# A whole chapter operated on at once, usually enumerating its sections:
#   "Chapter 205, Florida Statutes, consisting of ss. 205.013, 205.022, ...,
#    is repealed."
CHAPTER_OP = re.compile(
    r"\bChapter\s+(\d+[0-9A-Za-z.-]*)\s*,\s*Florida\s+Statutes\s*,"
    r"(?:\s*consisting\s+of\s+ss?\.\s*(.{0,4000}?))?"
    r"\s*,?\s*(?:is|are)\s+(repealed|amended|created|transferred|renumbered)",
    re.I | re.S)

# The operative verb, e.g. "... Florida Statutes, is amended to read:"
ACTION_VERB = re.compile(
    r"\b(?:is|are)\s+(amended|created|added|repealed|reenacted|redesignated|"
    r"renumbered|reordered|transferred|revived|readopted|directed)\b", re.I)
ADDED_TO = re.compile(r"\bis\s+added\s+to\s+section\b", re.I)

# What the formal title promises, e.g. "amending s. 215.422, F.S."
#
# The clause does not always end in "F.S." -- a long reenactment list often
# runs straight into "relating to ...", so terminate on any of the three
# closers actually used. Requiring "F.S." silently drops whole enumerations.
TITLE_REF = re.compile(
    r"\b(amending|creating|repealing|reenacting|renumbering|transferring|"
    r"redesignating|revising|abrogating)\s+s{1,2}\.\s*"
    r"([\d][0-9A-Za-z.()\s,]*?)"
    r"\s*(?:,?\s*F\.S\.|,\s*relating\s+to\b|;)", re.I)

ACTION_CATEGORY = {
    "amended": "amend", "amending": "amend", "revising": "amend",
    "created": "create", "creating": "create",
    "added": "amend",              # a subsection added to an existing statute
    "repealed": "repeal", "repealing": "repeal", "abrogating": "repeal",
    "reenacted": "reenact", "reenacting": "reenact",
    "redesignated": "renumber", "redesignating": "renumber",
    "renumbered": "renumber", "renumbering": "renumber",
    "reordered": "renumber",
    "transferred": "transfer", "transferring": "transfer",
    "revived": "revive", "readopted": "revive",
    "directed": "direct",
}

ACTION_LABEL = {
    "amend":    "amends",
    "create":   "creates",
    "repeal":   "repeals",
    "reenact":  "reenacts",
    "renumber": "renumbers",
    "transfer": "transfers",
    "revive":   "revives",
    "direct":   "directs",
}

WORD = re.compile(r"[A-Za-z0-9§().,;:'/-]+")

# A section number always carries a dot ("493.6201"). Requiring one keeps
# subsection markers out: "reenacting s. 493.6201(4), F.S." names one statute,
# not two, and without this the (4) is harvested as a phantom "s. 4".
CITE_IN_CLAUSE = re.compile(r"\b\d+[0-9A-Za-z]*\.\d+[0-9A-Za-z.]*")


def cites_in(clause: str) -> list[str]:
    cleaned = re.sub(r"\([^)]*\)", " ", clause)      # drop subsection markers
    return [c.rstrip(".") for c in CITE_IN_CLAUSE.findall(cleaned)]


def statute_url(cite: str, year: int = STATUTE_YEAR) -> str:
    return f"https://www.flsenate.gov/laws/statutes/{year}/{cite}"


@dataclass
class StatuteRef:
    statute: str
    action: str
    action_label: str
    bill_section: int | None = None
    scope: str = ""
    verb: str = ""
    line_start: int | None = None
    line_end: int | None = None
    words_added: int = 0
    words_deleted: int = 0
    in_title: bool = False
    in_body: bool = False

    @property
    def url(self) -> str:
        return statute_url(self.statute)

    def summary(self) -> str:
        where = f" at line {self.line_start}" if self.line_start else ""
        churn = ""
        if self.words_added or self.words_deleted:
            churn = (f" (+{self.words_added} / -{self.words_deleted} words)")
        return f"{self.action_label} s. {self.statute}{where}{churn}"

    def as_dict(self) -> dict:
        return {**self.__dict__, "url": self.url, "summary": self.summary()}


def parse_title(text: str) -> list[tuple[str, str]]:
    """What the bill's formal title says it does.

    One clause can name several statutes -- "amending ss. 1.01, 2.02, F.S." --
    so each citation in the clause is expanded against the same verb.
    """
    out: list[tuple[str, str]] = []
    head = normalize(text)[:20000]          # the title sits at the very front
    for m in TITLE_REF.finditer(head):
        verb = m.group(1).lower()
        action = ACTION_CATEGORY.get(verb, verb)
        for cite in cites_in(m.group(2)):
            if (action, cite) not in out:
                out.append((action, cite))
    return out


def _flatten(diff: BillDiff):
    """Concatenate segments, remembering where each one landed."""
    parts, spans, pos = [], [], 0
    for seg in diff.segments:
        text = normalize(seg.text)
        parts.append(text)
        spans.append((pos, pos + len(text), seg))
        pos += len(text) + 1
    return " ".join(parts), spans


def cross_reference(diff: BillDiff) -> list[StatuteRef]:
    """Every statute the bill operates on, with the churn in each section."""
    full, spans = _flatten(diff)
    if not full.strip():
        return []

    marks = [(m.start(), int(m.group(1))) for m in SECTION_MARK.finditer(full)]
    refs: list[StatuteRef] = []

    for i, (start, number) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(full)
        head = full[start:start + 320]      # the opener names statute and verb

        # A chapter-level operation names every section it sweeps up. Record
        # each one; otherwise a repeal of 40 statutes reads as a repeal of none.
        chapter = CHAPTER_OP.search(full[start:min(end, start + 4600)])
        if chapter and (not STATUTE_IN_SECTION.search(head)
                        or chapter.start() < 120):
            verb = chapter.group(3).lower()
            action = ACTION_CATEGORY.get(verb, "amend")
            members = cites_in(chapter.group(2) or "")
            line = next((sg.line for s0, s1, sg in spans
                         if s0 >= start and sg.line), None)
            for cite_no in (members or [chapter.group(1)]):
                refs.append(StatuteRef(
                    statute=cite_no.rstrip("."), action=action,
                    action_label=ACTION_LABEL.get(action, action),
                    bill_section=number,
                    scope=f"chapter {chapter.group(1)}", verb=verb,
                    line_start=line, in_body=True))
            continue

        cite = STATUTE_IN_SECTION.search(head) or STATUTE_SHORT.search(head)
        if not cite:
            continue
        vm = ACTION_VERB.search(head)
        verb = vm.group(1).lower() if vm else ("added" if ADDED_TO.search(head)
                                               else "amended")
        action = ACTION_CATEGORY.get(verb, "amend")

        scope = full[start + len(f"Section {number}. "):cite.start() + start]
        scope = re.sub(r"\s+", " ", scope).strip(" ,")

        added = deleted = 0
        line_start = line_end = None
        for s0, s1, seg in spans:
            if s1 <= start or s0 >= end:
                continue
            if seg.line:
                line_start = seg.line if line_start is None else min(line_start, seg.line)
                line_end = seg.line if line_end is None else max(line_end, seg.line)
            if seg.kind == INSERT:
                added += len(WORD.findall(seg.text))
            elif seg.kind == DELETE:
                deleted += len(WORD.findall(seg.text))

        refs.append(StatuteRef(
            statute=cite.group(1).rstrip("."),
            action=action, action_label=ACTION_LABEL.get(action, action),
            bill_section=number, scope=scope[:160], verb=verb,
            line_start=line_start, line_end=line_end,
            words_added=added, words_deleted=deleted, in_body=True))

    # Fold in anything the title promises that no section header revealed.
    seen = {(r.action, r.statute) for r in refs}
    by_statute = {r.statute for r in refs}
    for action, cite in parse_title(full):
        if (action, cite) in seen:
            for r in refs:
                if r.statute == cite and r.action == action:
                    r.in_title = True
            continue
        refs.append(StatuteRef(
            statute=cite, action=action,
            action_label=ACTION_LABEL.get(action, action),
            in_title=True, in_body=cite in by_statute))

    refs.sort(key=lambda r: (r.bill_section is None, r.bill_section or 0,
                             r.statute))
    return refs
