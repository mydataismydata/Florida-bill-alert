"""Check every quote against the bill before anything is published.

A model asked for a verbatim span will sometimes paraphrase, merge two
passages, or invent one outright. Checking the span against the source catches
all three cheaply, and it is the difference between a claim a reader can
verify in ten seconds and one they have to take on faith.

Claims that fail are dropped, not softened. What was dropped is recorded, so
the failure rate is visible rather than hidden.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..diff import normalize
from .brief import strip_markers
from .schema import QUOTE

EXACT, LOOSE, FAILED = "exact", "loose", "failed"

# A quote the decoder closed at its length limit was cut, usually mid-word:
# SB 686 came back "...the parcel or en" for "the parcel or parcels". The bill
# says where each word really ends, so a cut quote is trimmed against it rather
# than guessed at. One that matches loses its last word only if the bill shows
# that word continuing; one that fails is shortened a word at a time until it
# matches, but never below six words. Either way what is left is verbatim.
_QUOTE_CUT = QUOTE["maxLength"] * 0.97
_MIN_CUT_WORDS = 6
_MAX_CUT_WORDS = 3

# A grammar guarantees well-formed output, not useful output. Cornered by a
# length floor it cannot satisfy, a model will pad -- emitting the same
# character until the token budget runs out. That is well-formed, on-schema,
# and worthless, so it has to be detected rather than trusted.
# DOTALL matters: the padding actually observed was newlines, and without it
# "." never matches one, so the run goes undetected whenever the braces happen
# to balance.
_RUN = re.compile(r"(.)\1{24,}", re.DOTALL)


def is_degenerate(text: str) -> str | None:
    """Return why this output is unusable, or None if it looks real."""
    if not text or not text.strip():
        return "empty response"
    run = _RUN.search(text)
    if run:
        return f"padded with repeated {run.group(1)!r}"
    stripped = text.strip()
    if stripped.startswith("{") and not stripped.endswith("}"):
        return "truncated before the object closed"
    return None

_WS = re.compile(r"\s+")
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")


def canonical(text: str) -> str:
    """Whitespace and typography vary between the sources; meaning does not."""
    return _WS.sub(" ", normalize(strip_markers(text or ""))).strip()


def loose_key(text: str) -> str:
    return _NOT_ALNUM.sub("", canonical(text).lower())


@dataclass
class Check:
    status: str
    quote: str
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (EXACT, LOOSE)


class Verifier:
    """Holds one bill's text and answers whether a quote really came from it.

    An amendatory bill has no single text. Where it substitutes a word inline
    the document carries both -- SB 100 replaces the year twelve times, so
    running the segments together reads "Florida Statutes 2026 2025", which is
    a sentence that exists in no version of the law. A model quoting either
    the amended reading or the existing one is quoting the bill correctly, and
    checking against the concatenation rejected both: 41.8% of everything ever
    dropped was a real quote thrown away on this.

    So a quote is checked against three readings. What the law will say
    (plain + additions), what it says now (plain + deletions), and the
    document as printed, which is the only one that contains a passage
    spanning an edit on both sides.
    """

    def __init__(self, source, min_words: int = 3):
        # A plain string is still accepted: it is one reading.
        texts = source if isinstance(source, (list, tuple)) else [source]
        self.canons = [canonical(t) for t in texts if t]
        self.looses = [loose_key(t) for t in texts if t]
        self.min_words = min_words

    @property
    def canon(self) -> str:            # kept for callers that inspect it
        return self.canons[0] if self.canons else ""

    def check(self, quote: str) -> Check:
        q = canonical(quote)
        if not q:
            return Check(FAILED, quote, "empty quote")
        if len(q.split()) < self.min_words:
            return Check(FAILED, quote, "too short to identify a passage")
        if any(q in c for c in self.canons):
            return Check(EXACT, q)
        lk = loose_key(quote)
        if lk and any(lk in l for l in self.looses):
            # same words, different punctuation or spacing
            return Check(LOOSE, q)
        return Check(FAILED, quote, "not found in the bill text")

    def recheck_cut(self, quote: str, first: Check) -> Check:
        """Trim a quote cut at its length limit back to where the bill agrees."""
        if first.status == EXACT:
            q = first.quote
            for c in self.canons:
                i = c.find(q)
                if i < 0:
                    continue
                nxt = c[i + len(q):i + len(q) + 1]
                if nxt.isalnum() and q[-1:].isalnum() and " " in q:
                    shorter = self.check(q.rsplit(" ", 1)[0])
                    return shorter if shorter.ok else first
                break
            return first
        # A loose match ignores spaces, so a half-written word still passes:
        # "...requirement from thep" matches "from the purchaser". On a cut
        # quote, shortening to an exact match is preferred to keeping that.
        # At most three words: a cut garbles the last word or two. Shortening
        # further would rescue a quote that was wrong in the middle by its
        # opening words, which is no longer evidence for the claim.
        words = (quote or "").split()
        fallback = first if first.ok else None
        for k in range(1, min(_MAX_CUT_WORDS, len(words) - _MIN_CUT_WORDS) + 1):
            shorter = self.check(" ".join(words[:-k]))
            if shorter.status == EXACT:
                return shorter
            if shorter.ok and fallback is None:
                fallback = shorter
        return fallback or first


def verify_claims(claims: list[dict], verifier: Verifier,
                  field: str = "quote") -> tuple[list[dict], list[dict]]:
    """Split claims into those whose quote checks out and those that do not."""
    kept, dropped = [], []
    for claim in claims:
        result = verifier.check(claim.get(field, ""))
        if len(claim.get(field) or "") >= _QUOTE_CUT:
            result = verifier.recheck_cut(claim.get(field, ""), result)
        entry = dict(claim)
        entry["quote_status"] = result.status
        if result.ok:
            entry[field] = result.quote
            kept.append(entry)
        else:
            entry["drop_reason"] = result.reason
            dropped.append(entry)
    return kept, dropped
