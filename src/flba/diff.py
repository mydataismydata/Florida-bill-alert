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


def parse(path_or_html, chamber: str) -> BillDiff:
    """Dispatch on chamber; both return the same structure."""
    if chamber.lower().startswith("s"):
        return parse_senate_html(path_or_html)
    return parse_house_pdf(path_or_html)
