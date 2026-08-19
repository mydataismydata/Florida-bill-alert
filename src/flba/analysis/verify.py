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

EXACT, LOOSE, FAILED = "exact", "loose", "failed"

# A grammar guarantees well-formed output, not useful output. Cornered by a
# length floor it cannot satisfy, a model will pad -- emitting the same
# character until the token budget runs out. That is well-formed, on-schema,
# and worthless, so it has to be detected rather than trusted.
_RUN = re.compile(r"(.)\1{24,}")


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
    """Holds one bill's text and answers whether a quote really came from it."""

    def __init__(self, source: str, min_words: int = 3):
        self.canon = canonical(source)
        self.loose = loose_key(source)
        self.min_words = min_words

    def check(self, quote: str) -> Check:
        q = canonical(quote)
        if not q:
            return Check(FAILED, quote, "empty quote")
        if len(q.split()) < self.min_words:
            return Check(FAILED, quote, "too short to identify a passage")
        if q in self.canon:
            return Check(EXACT, q)
        lk = loose_key(quote)
        if lk and lk in self.loose:
            # same words, different punctuation or spacing
            return Check(LOOSE, q)
        return Check(FAILED, quote, "not found in the bill text")


def verify_claims(claims: list[dict], verifier: Verifier,
                  field: str = "quote") -> tuple[list[dict], list[dict]]:
    """Split claims into those whose quote checks out and those that do not."""
    kept, dropped = [], []
    for claim in claims:
        result = verifier.check(claim.get(field, ""))
        entry = dict(claim)
        entry["quote_status"] = result.status
        if result.ok:
            entry[field] = result.quote
            kept.append(entry)
        else:
            entry["drop_reason"] = result.reason
            dropped.append(entry)
    return kept, dropped
