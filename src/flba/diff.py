"""Extract what a bill adds and deletes, for either chamber.

This is the deterministic core of the project. Florida bills show new language
underlined and removed language struck through; recovering that mechanically
gives claims no language model can fabricate -- "this bill deletes the words
'shall provide public notice' from s. 286.011, at line 412" is checkable in
ten seconds by anyone holding the bill.

The convention is identical in both chambers, but it is encoded differently:

  Senate  HTML with <u class="Insert"> and <s class="Remove">. Trivial.
  House   PDF only. Body text is black, so colour is no help; the formatting
          is drawn as thin rules over the glyphs. A rule below the baseline is
          an underline, one through the middle is a strikethrough.

Both paths produce the same Segment list, so everything downstream is
chamber-agnostic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

INSERT, DELETE, PLAIN = "insert", "delete", "plain"

# The HTML uses typographic punctuation while the PDF yields ASCII, so
# "applicant’s" and "applicant's" must not tokenise differently.
_WS = re.compile(r"\s+")

PUNCT = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u00a0": " ", "\u2007": " ", "\u202f": " ", "\ufeff": "",
})


def normalize(text: str) -> str:
    return text.translate(PUNCT)

# A rule's height as a fraction of the glyph box: 0 is the top of the
# characters, 1 the bottom. Underlines measure ~1.04, strikethroughs ~0.53,
# so anything below this threshold is a strike. The clusters are far apart --
# see docs/INGEST-NOTES.md for the measurements.
STRIKE_MAX_FRAC = 0.78
MAX_RULE_HEIGHT = 1.5        # thicker rects are borders and table rules
MIN_RULE_WIDTH = 2.0
COVERAGE = 0.45              # rule must span this much of a word to mark it

# Page furniture to drop: everything above the first body line and below the
# last. The legend ("words stricken are deletions...") is itself struck and
# underlined, so leaving it in would poison every page with a false hit.
BODY_TOP, BODY_BOTTOM = 120.0, 650.0
LINENO_MAX_X = 60.0          # line numbers sit in the left margin


@dataclass
class Segment:
    kind: str
    text: str
    line: int | None = None
    page: int | None = None


@dataclass
class BillDiff:
    source: str
    segments: list[Segment] = field(default_factory=list)

    def _of(self, kind):
        return [s for s in self.segments if s.kind == kind]

    @property
    def insertions(self) -> list[Segment]:
        return self._of(INSERT)

    @property
    def deletions(self) -> list[Segment]:
        return self._of(DELETE)

    def words(self, kind) -> list[str]:
        out: list[str] = []
        for s in self._of(kind):
            for tok in re.findall(r"[A-Za-z0-9§().,;:'/-]+", normalize(s.text)):
                # A token ending in "-" is a hyphenated word the PDF broke
                # across a line ("end-of-" / "course"). Legislative drafting
                # does not hyphenate to wrap, so the hyphen is real: keep it.
                if out and out[-1].endswith("-"):
                    out[-1] += tok
                else:
                    out.append(tok)
        return out

    def stats(self) -> dict:
        return {
            "source": self.source,
            "segments": len(self.segments),
            "insertions": len(self.insertions),
            "deletions": len(self.deletions),
            "words_inserted": len(self.words(INSERT)),
            "words_deleted": len(self.words(DELETE)),
        }


# --------------------------------------------------------------------------
# Senate: HTML
# --------------------------------------------------------------------------

def _senate_kind(node) -> str:
    """Mark a text node by the nearest Insert/Remove ancestor."""
    for parent in node.parents:
        cls = " ".join(parent.get("class") or []) if parent.name else ""
        if "Insert" in cls or parent.name == "ins":
            return INSERT
        if "Remove" in cls or parent.name in ("s", "strike", "del"):
            return DELETE
        if parent.name == "u":
            return INSERT
    return PLAIN


def parse_senate_html(html: str) -> BillDiff:
    """Walk text nodes character-by-character, mirroring the PDF path.

    Collecting whole <u>/<s> elements instead looks simpler but is wrong: the
    generator splits runs mid-word across adjacent tags, so joining elements
    with a space yields "applica bility" and "department s".

    Bill text is preformatted with the legislature's own line numbers at the
    start of each line. Those are what amendments cite ("between lines 17 and
    18, insert"), so they are carried onto every segment.
    """
    from bs4 import BeautifulSoup, NavigableString

    soup = BeautifulSoup(html, "lxml")
    body = soup.find("pre") or soup.find("body") or soup
    diff = BillDiff(source="senate-html")

    chars: list[tuple[str, str]] = []
    for node in body.descendants:
        if not isinstance(node, NavigableString):
            continue
        if node.parent.name in ("style", "script", "xml", "title"):
            continue
        kind = _senate_kind(node)
        chars.extend((kind, ch) for ch in str(node))

    def flush(row, line_no):
        run_kind, run = None, []
        for kind, ch in row:
            if kind != run_kind and run:
                text = re.sub(r"\s+", " ", "".join(run)).strip()
                if text:
                    diff.segments.append(Segment(run_kind, text, line_no))
                run = []
            run_kind = kind
            run.append(ch)
        if run:
            text = re.sub(r"\s+", " ", "".join(run)).strip()
            if text:
                diff.segments.append(Segment(run_kind, text, line_no))

    line_no, row = None, []
    for kind, ch in chars:
        if ch == "\n":
            flush(row, line_no)
            row, line_no = [], None
            continue
        row.append((kind, ch))
        if line_no is None:
            lead = "".join(c for _, c in row)
            m = re.match(r"^\s{0,12}(\d{1,4})\s$", lead)
            if m:
                line_no = int(m.group(1))
                row = []                      # the number is not bill text
    flush(row, line_no)

    return diff



# --------------------------------------------------------------------------
# House: PDF geometry
# --------------------------------------------------------------------------

def _rules(page):
    """Thin horizontal rules Word draws for underline and strikethrough."""
    out = []
    for r in list(page.rects) + list(page.lines):
        h = abs(r.get("height", 0)) or abs(r["y1"] - r["y0"])
        w = abs(r.get("width", 0)) or abs(r["x1"] - r["x0"])
        if h <= MAX_RULE_HEIGHT and w >= MIN_RULE_WIDTH:
            out.append(r)
    return out


def _classify(word, rules) -> str:
    """Mark a word by the rule drawn over it, if any."""
    best, best_cov = PLAIN, 0.0
    ww = max(word["x1"] - word["x0"], 0.01)
    for r in rules:
        overlap = min(word["x1"], r["x1"]) - max(word["x0"], r["x0"])
        if overlap <= 0:
            continue
        # the rule has to belong to this line of text, not the one below
        if not (word["top"] - 3 <= r["top"] <= word["bottom"] + 4):
            continue
        cov = overlap / ww
        if cov < COVERAGE or cov <= best_cov:
            continue
        frac = (r["top"] - word["top"]) / max(word["bottom"] - word["top"], 0.01)
        best, best_cov = (DELETE if frac < STRIKE_MAX_FRAC else INSERT), cov
    return best


def parse_house_pdf(path: str | Path, pages=None) -> BillDiff:
    import pdfplumber

    diff = BillDiff(source="house-pdf")
    with pdfplumber.open(str(path)) as pdf:
        targets = pdf.pages if pages is None else [pdf.pages[i] for i in pages]
        for page in targets:
            rules = _rules(page)
            words = [w for w in page.extract_words()
                     if BODY_TOP <= w["top"] <= BODY_BOTTOM]
            if not words:
                continue

            lines: dict[float, list] = {}
            for w in words:
                lines.setdefault(round(w["top"] / 4) * 4, []).append(w)

            for _, row in sorted(lines.items()):
                row.sort(key=lambda w: w["x0"])
                lineno = None
                if row and row[0]["x0"] < LINENO_MAX_X and row[0]["text"].isdigit():
                    lineno = int(row[0]["text"])
                    row = row[1:]

                # run-length encode consecutive words sharing a mark
                run_kind, run = None, []
                for w in row:
                    k = _classify(w, rules)
                    if k != run_kind and run:
                        diff.segments.append(Segment(
                            run_kind, " ".join(run), lineno, page.page_number))
                        run = []
                    run_kind = k
                    run.append(w["text"])
                if run:
                    diff.segments.append(Segment(
                        run_kind, " ".join(run), lineno, page.page_number))

    diff.segments = [s for s in diff.segments if s.kind != PLAIN or s.text.strip()]
    return diff


# --------------------------------------------------------------------------
# Presenting a diff
# --------------------------------------------------------------------------

SENTENCE_END = re.compile(r"[.;:]\s")


@dataclass
class ContextBlock:
    """A run of changes together with the words around them.

    An isolated fragment tells a reader nothing -- "does" or "shall be
    assessed" could mean anything. What matters is the sentence it sits in,
    so changes are shown inside their surrounding text with the added and
    deleted words marked in place.
    """
    line: int | None
    page: int | None
    parts: list[Segment] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return sum(1 for p in self.parts if p.kind != PLAIN)


# A sentence ends at a full stop, question or exclamation mark -- with any
# closing quote and a section catchline's dash -- before a capital or "(".
# "s. 456.072" and "F.S." are not ends; statute text is full of both. Text
# taken from the bill's PDF sometimes loses the space at a line break --
# "an effective date.Be It Enacted" -- so a capitalised word straight after
# the stop also counts; "U.S.C." does not, because "S" is followed by a stop.
_SENTENCE_END = re.compile(r"[.!?][\"'”’)]?(?:—|--)?"
                           r"(?:\s+(?=[A-Z(\"“])|(?=[A-Z][a-z]))")
_NOT_AN_END = frozenset("s ss f.s u.s u.s.c e.g i.e fla stat no nos st inc co "
                        "corp jr sr dr mr ms mrs vs v ch art sec".split())
# How far back to look for the start of a sentence before giving up and
# keeping the old start. Statute sentences run long, but not this long.
_SENTENCE_REACH = 600


def _back_to_sentence(segs: list[Segment], lo: int) -> tuple[int, int]:
    """Move a block's start back to the beginning of the sentence it opens in.

    Looks only at text before the old start, so a block never loses a
    sentence it already showed. Returns the index of the new first segment and
    how many of its characters to skip. Only unmarked text is cut: a deletion
    or insertion is shown whole or not at all.
    """
    j, reach = lo, 0
    while j > 0 and reach < _SENTENCE_REACH:
        j -= 1
        reach += len(segs[j].text)
    # Segments are joined with a space, as the brief prints them, plus the
    # first characters of the old start so a sentence ending right before it
    # is still seen as one.
    starts, pos = [], 0
    for k in range(j, lo):
        starts.append(pos)
        pos += len(segs[k].text) + 1
    boundary = pos
    window = " ".join(s.text for s in segs[j:lo]) + " " + segs[lo].text[:3]

    cut = None
    for m in _SENTENCE_END.finditer(window):
        if m.end() > boundary:
            break
        word = re.search(r"([\w.]+)$", window[:m.start()])
        if word and (word.group(1).lower() in _NOT_AN_END or len(word.group(1)) == 1):
            continue
        cut = m.end()

    if cut is None:
        # No sentence end within reach: the sentence began at the top of the
        # document, or is too long to chase. Keep the old start in that case.
        return (0, 0) if j == 0 and reach < _SENTENCE_REACH else (lo, 0)
    if cut >= boundary:
        return lo, 0                      # the block already opens a sentence
    k = max(i for i, s in enumerate(starts) if s <= cut)
    seg, off = segs[j + k], cut - starts[k]
    if off >= len(seg.text):
        return j + k + 1, 0
    return (j + k, off) if seg.kind == PLAIN else (j + k, 0)


def context_blocks(diff: BillDiff, pad: int = 220,
                   max_blocks: int = 0, join_gap: int = 400,
                   whole_sentences: bool = False) -> list[ContextBlock]:
    """Group each change with the text around it.

    Changes closer together than `join_gap` characters share one block, so a
    heavily edited sentence reads as one passage rather than being repeated
    once per altered word.

    With `whole_sentences`, each block starts where its first sentence does
    rather than at a line break. Line breaks fall mid-sentence: SB 192's block
    opened "patient, the value of which is greater than $501", and a model
    shown only that could not say what the $501 applied to -- the subject,
    "Failing to preserve identity of funds and property of a", was on the line
    before.
    """
    segs = diff.segments
    if not segs:
        return []

    marked = [i for i, s in enumerate(segs) if s.kind != PLAIN]
    if not marked:
        return []

    # walk outward from each change until the character budget is spent
    spans: list[tuple[int, int, int]] = []
    for i in marked:
        lo = hi = i
        budget = pad
        while lo > 0 and budget > 0:
            lo -= 1
            budget -= len(segs[lo].text)
        budget = pad
        while hi < len(segs) - 1 and budget > 0:
            hi += 1
            budget -= len(segs[hi].text)
        skip = 0
        if whole_sentences:
            lo, skip = _back_to_sentence(segs, lo)
        spans.append((lo, hi, skip))

    merged: list[list[int]] = []
    for lo, hi, skip in spans:
        if merged and lo <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi, skip])

    blocks = []
    for lo, hi, skip in merged:
        parts = segs[lo:hi + 1]
        if skip:
            first = parts[0]
            parts = [Segment(first.kind, first.text[skip:], first.line, first.page),
                     *parts[1:]]
        line = next((p.line for p in parts if p.line), None)
        page = next((p.page for p in parts if p.page), None)
        blocks.append(ContextBlock(line=line, page=page, parts=list(parts)))
    return blocks[:max_blocks] if max_blocks else blocks


def lines(diff: BillDiff) -> list[dict]:
    """Regroup segments into the bill's own numbered lines, for full display.

    Segments break wherever the marking changes, so several belong to one
    printed line; the line number is what the Legislature cites and what an
    amendment refers to, which makes it the right unit to render and anchor.
    """
    out: list[dict] = []
    cur: dict | None = None
    for seg in diff.segments:
        if cur is None or (seg.line is not None and seg.line != cur["line"]):
            cur = {"line": seg.line, "page": seg.page, "parts": [],
                   "changed": False}
            out.append(cur)
        cur["parts"].append(seg)
        if seg.kind != PLAIN:
            cur["changed"] = True
    return out


def locate(diff: BillDiff, quote: str) -> int | None:
    """Find the line a quoted span sits on.

    Lets a claim link to the exact place in the bill it rests on, rather than
    to the statute in general -- the reader lands on the words themselves.
    """
    target = _WS.sub(" ", normalize(quote or "")).strip().lower()
    if len(target) < 12:
        return None
    head = target[:60]
    running, spans = [], []
    for seg in diff.segments:
        text = _WS.sub(" ", normalize(seg.text)).strip().lower()
        spans.append((sum(len(t) + 1 for t in running), seg))
        running.append(text)
    joined = " ".join(running)
    at = joined.find(head)
    if at < 0:
        return None
    best = None
    for start, seg in spans:
        if start <= at and seg.line:
            best = seg.line
        elif start > at:
            break
    return best


def parse(path_or_html, chamber: str) -> BillDiff:
    """Dispatch on chamber; both return the same structure."""
    if chamber.lower().startswith("s"):
        return parse_senate_html(path_or_html)
    return parse_house_pdf(path_or_html)
