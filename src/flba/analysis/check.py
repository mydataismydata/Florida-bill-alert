"""Re-read every published sentence against the bill, and correct what it contradicts.

The quote verifier proves a quote is in the bill. It says nothing about the
prose written beside the quote, and the summary has no quote at all, so the
sentences a reader actually reads were never checked. Two of them were wrong
in ways the bill shows at a glance:

- HB 1461 strikes "municipal" from "each municipal electric utility shall
  submit ... a report". Every electric utility now has the duty. The summary
  said municipal utilities "no longer must file".
- SB 166 strikes "and provide treatment" from what a provider may do for a
  minor without a parent's consent. The summary said a minor may still be
  "examined and treated" without it.

So the draft goes back to the model as numbered sentences, with the same brief
and one job: say which sentences the bill shows to be false. An objection is
held to the standard every claim is held to. It must quote the bill words
that show the sentence wrong, and one whose quote is not in the bill is
ignored. A wrong sentence is replaced by the correction offered, or removed
when none is. Every change is recorded with its old and new wording, so a
correction can be reviewed like any other output.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import passes as P
from .schema import ONE_LINE_MAX, PROVISIONS
from .verify import is_degenerate, verify_claims

_HEADING_MAX = PROVISIONS["properties"]["provisions"]["items"]["properties"][
    "heading"]["maxLength"]


def split_sentences(text: str) -> list[str]:
    """Sentences, using the same abbreviation rules as the cut-text trimmer."""
    text = " ".join((text or "").split())
    out, start = [], 0
    for end in P._sentence_ends(text):
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        start = end
    rest = text[start:].strip()
    if rest:
        out.append(rest)
    return out


@dataclass
class _Draft:
    """The prose a reader sees, held as sentences so one can be swapped out."""
    one_line: str | None
    paragraphs: list[list[str | None]]
    headings: list[str]
    effects: list[list[str | None]]

    def items(self) -> list[tuple[tuple, str, str]]:
        out = []
        if self.one_line:
            out.append((("one_line",), "headline", self.one_line))
        for i, para in enumerate(self.paragraphs):
            for j, s in enumerate(para):
                if s:
                    out.append((("summary", i, j), "summary", s))
        for k, heading in enumerate(self.headings):
            out.append((("heading", k), f"provision {k + 1} heading", heading))
            for j, s in enumerate(self.effects[k]):
                if s:
                    out.append((("effect", k, j),
                                f"provision {k + 1} effect", s))
        return out

    def set(self, path: tuple, text: str | None) -> None:
        kind = path[0]
        if kind == "one_line":
            self.one_line = text
        elif kind == "summary":
            self.paragraphs[path[1]][path[2]] = text
        elif kind == "heading":
            self.headings[path[1]] = text
        else:
            self.effects[path[1]][path[2]] = text


def _note(items) -> str:
    lines = [f"DRAFT TO CHECK -- {len(items)} sentences:"]
    for n, (_path, where, text) in enumerate(items, 1):
        lines.append(f"{n}. ({where}) {text}")
    return "\n".join(lines)


def _bad_fix(path: tuple, fix: str, deleted_text: str) -> str | None:
    """Why a correction cannot be used as written, or None if it can."""
    hit = P.flags_intent(fix)
    if hit:
        return f"correction speculates about motive ({hit!r})"
    if path[0] in ("one_line", "summary"):
        hit = P.flags_false_repeal(fix, deleted_text)
        if hit:
            return f"correction {hit}"
    if path[0] == "one_line":
        if len(fix) > ONE_LINE_MAX:
            return "corrected headline is longer than the headline limit"
        hit = P.flags_cut_headline(fix)
        if hit:
            return f"corrected headline {hit}"
    if path[0] == "heading" and len(fix) > _HEADING_MAX:
        return "corrected heading is longer than the heading limit"
    return None


@dataclass
class Checked:
    summary: dict
    provisions: list
    corrections: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _ask(backend, brief_text: str, note: str, log, where: str):
    """One check request, retried once if the answer is unusable."""
    extra = ""
    for attempt in (1, 2):
        reply = backend.chat(P.messages(brief_text, "check", note + extra),
                             schema=P.SCHEMAS["check"],
                             max_tokens=P.BUDGET["check"])
        why = is_degenerate(reply.text)
        if why is None:
            try:
                return P.trim_cut(reply.json(), P.SCHEMAS["check"]), None
            except json.JSONDecodeError:
                why = "unparseable JSON"
        log(f"    check{where}: attempt {attempt} unusable ({why})")
        extra = "\n\n" + P._MALFORMED
    return None, why


def check(summary: dict | None, provisions: list, brief_text: str, verifier,
          backend, deleted_text: str = "", log=lambda *_: None,
          where: str = "") -> Checked:
    """Check the headline, summary and provisions against the bill text.

    Either part may be empty: a bill read in parts checks each part's
    provisions against that part, and the summary against the digest it was
    written from.
    """
    summary = dict(summary or {})
    provisions = [dict(p) for p in provisions or []]
    draft = _Draft(
        one_line=summary.get("one_line") or None,
        paragraphs=[split_sentences(p) for p in summary.get("summary") or []],
        headings=[p.get("heading", "") for p in provisions],
        effects=[split_sentences(p.get("effect", "")) for p in provisions],
    )
    out = Checked(summary=summary, provisions=provisions)
    checked = rounds = 0

    for rnd in range(1, P.CHECK_ROUNDS + 1):
        items = draft.items()
        if not items:
            break
        rounds = rnd
        checked = max(checked, len(items))
        data, why = _ask(backend, brief_text, _note(items), log, where)
        if data is None:
            out.failures.append({"pass": "check", "round": rnd,
                                 "reason": why, "where": where.strip()})
            break

        changed, seen = 0, set()
        for entry in data.get("checks") or []:
            n = entry.get("n")
            if (entry.get("verdict") != "wrong" or not isinstance(n, int)
                    or not 1 <= n <= len(items) or n in seen):
                continue
            seen.add(n)
            path, label, was = items[n - 1]
            record = {"round": rnd, "where": label, "was": was,
                      "problem": " ".join((entry.get("problem") or "").split()),
                      "quote": entry.get("quote") or ""}
            # An objection with no evidence in the bill is the checker's
            # own misreading, and acting on it would publish that instead.
            kept, dropped = verify_claims([{"quote": record["quote"]}], verifier)
            if not kept:
                record["rejected_for"] = ("objection's quote is not in the bill"
                                          f" ({dropped[0].get('drop_reason')})")
                out.corrections.append(record)
                continue
            record["quote"] = kept[0]["quote"]
            record["quote_status"] = kept[0]["quote_status"]

            fix = " ".join((entry.get("fix") or "").split())
            bad = _bad_fix(path, fix, deleted_text) if fix else None
            if bad:
                record["fix_rejected"] = bad
                fix = ""
            if not fix and path[0] in ("one_line", "heading"):
                # A headline or heading cannot simply be removed. The
                # objection stays on record for review.
                record["rejected_for"] = bad or "no correction offered"
                out.corrections.append(record)
                continue
            draft.set(path, fix or None)
            record["now"] = fix
            out.corrections.append(record)
            changed += 1
        log(f"    check{where}: round {rnd}, {len(items)} sentences, "
            f"{changed} corrected or removed")
        if not changed:
            break

    if draft.one_line:
        summary["one_line"] = draft.one_line
    if "summary" in summary:
        summary["summary"] = [" ".join(s for s in para if s)
                              for para in draft.paragraphs if any(para)]
    kept_prov = []
    for k, prov in enumerate(provisions):
        effect = " ".join(s for s in draft.effects[k] if s)
        if not effect:
            out.removed.append(dict(prov, drop_reason="check: every sentence "
                                    "of the effect was contradicted by the bill"))
            continue
        prov["heading"] = draft.headings[k]
        prov["effect"] = effect
        kept_prov.append(prov)
    out.provisions = kept_prov

    applied = [c for c in out.corrections if "now" in c]
    out.stats = {"rounds": rounds, "sentences": checked,
                 "corrected": sum(1 for c in applied if c["now"]),
                 "removed": sum(1 for c in applied if not c["now"]),
                 "rejected": len(out.corrections) - len(applied)}
    return out
