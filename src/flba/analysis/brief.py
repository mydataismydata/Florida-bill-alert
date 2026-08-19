"""Turn a bill into what the model reads.

Raw bill text is the wrong input. An amendatory bill's meaning lives in what
it adds and removes from existing law, and that is already extracted
deterministically -- so the brief leads with established facts (which statutes,
which action, which lines) and then shows the changed language itself.

Three things follow from that. The model spends its context on substance
rather than boilerplate; every fact in the brief is one a reader can check;
and the quotes it produces can be verified against the source, because the
source is what it was shown.
"""
from __future__ import annotations

import json
import re

from ..diff import DELETE, INSERT, PLAIN, BillDiff, Segment, context_blocks

# Deliberately unlikely to occur in statute text, and stripped again before a
# quote is checked against the bill.
ADD_OPEN, ADD_CLOSE = "[[+", "+]]"
DEL_OPEN, DEL_CLOSE = "[[-", "-]]"
MARKERS = re.compile(r"\[\[[+-]|[+-]\]\]")

DEFAULT_BUDGET = 24_000     # characters of changed language


def strip_markers(text: str) -> str:
    return MARKERS.sub("", text)


def render_passage(parts) -> str:
    out = []
    for p in parts:
        if p.kind == INSERT:
            out.append(f"{ADD_OPEN}{p.text}{ADD_CLOSE}")
        elif p.kind == DELETE:
            out.append(f"{DEL_OPEN}{p.text}{DEL_CLOSE}")
        else:
            out.append(p.text)
    return " ".join(out)


def build(bill: dict, diff: BillDiff, refs: list[dict],
          budget: int = DEFAULT_BUDGET) -> dict:
    """Return {'text': brief, 'truncated': bool, 'passages': n_shown}."""
    lines = [
        f"BILL: {bill.get('label','')} — {bill.get('title','')}",
    ]
    if bill.get("chamber"):
        lines.append(f"CHAMBER: {bill['chamber']}")
    if bill.get("sponsor"):
        lines.append(f"SPONSOR: {bill['sponsor']}")
    if bill.get("effective_date"):
        lines.append(f"EFFECTIVE: {bill['effective_date']}")
    if bill.get("long_title"):
        lines.append(f"\nOFFICIAL SUMMARY (written by the Legislature):\n"
                     f"{bill['long_title']}")

    if refs:
        lines.append(f"\nSTATUTES THIS BILL OPERATES ON ({len(refs)}):")
        for r in refs[:60]:
            where = f"line {r['line_start']}" if r.get("line_start") else "title only"
            churn = ""
            if r.get("words_added") or r.get("words_deleted"):
                churn = f", +{r['words_added']}/-{r['words_deleted']} words"
            scope = f" [{r['scope']}]" if r.get("scope") else ""
            lines.append(f"  section {r.get('bill_section') or '-'}: "
                         f"{r['action']}s s. {r['statute']} at {where}{churn}{scope}")
        if len(refs) > 60:
            lines.append(f"  ... and {len(refs) - 60} more")

    blocks = context_blocks(diff)
    # Substance concentrates in the biggest edits, so when the budget binds,
    # keep those and say so rather than silently cutting the tail.
    ordered = sorted(enumerate(blocks), key=lambda kv: -kv[1].changed)
    keep, used = [], 0
    for idx, blk in ordered:
        body = render_passage(blk.parts)
        if used + len(body) > budget and keep:
            continue
        keep.append((idx, blk, body))
        used += len(body)
    keep.sort(key=lambda kv: kv[0])

    if keep:
        lines.append(
            f"\nCHANGED LANGUAGE ({len(keep)} of {len(blocks)} passages). "
            f"{ADD_OPEN}text{ADD_CLOSE} is being ADDED to law; "
            f"{DEL_OPEN}text{DEL_CLOSE} is being DELETED from law. "
            f"Line numbers are the Legislature's own.")
        for _, blk, body in keep:
            at = f"line {blk.line}" if blk.line else "—"
            lines.append(f"\n[{at}] {body}")
    else:
        lines.append("\nCHANGED LANGUAGE: none marked "
                     "(this bill creates new text rather than amending law).")

    return {
        "text": "\n".join(lines),
        "truncated": len(keep) < len(blocks),
        "passages": len(keep),
        "total_passages": len(blocks),
    }


def source_text(diff: BillDiff) -> str:
    """The full bill as one string, for verifying quotes against."""
    return " ".join(s.text for s in diff.segments)
