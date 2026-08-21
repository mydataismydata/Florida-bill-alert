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


def runs(parts):
    """Group consecutive parts that share a kind."""
    grouped = []
    for p in parts:
        if grouped and grouped[-1][0] == p.kind:
            grouped[-1][1].append(p.text)
        else:
            grouped.append((p.kind, [p.text]))
    return grouped


def render_passage(parts) -> str:
    """One marker span per contiguous edit, not one per line.

    The parser emits a segment per line of the Legislature's own layout, so a
    single added sentence arrives as four consecutive INSERT segments. Marking
    each separately put 2.4x more markers in the brief than there were edits,
    and chopped added sentences into fragments -- which is how a bill that
    added a prohibition came back summarised as repealing one. Runs are joined
    with the same single space used between parts, so the text itself is
    unchanged and quotes still verify against the source.
    """
    out = []
    for kind, texts in runs(parts):
        body = " ".join(texts)
        if kind == INSERT:
            out.append(f"{ADD_OPEN}{body}{ADD_CLOSE}")
        elif kind == DELETE:
            out.append(f"{DEL_OPEN}{body}{DEL_CLOSE}")
        else:
            out.append(body)
    return " ".join(out)


def header(bill: dict, refs: list[dict]) -> list[str]:
    """Everything above the changed language: who filed it, what it operates
    on. Identical in every chunk of a split brief, which is what lets the
    model read part four without having lost the bill's identity."""
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
    return lines


def build(bill: dict, diff: BillDiff, refs: list[dict],
          budget: int = DEFAULT_BUDGET) -> dict:
    """Return {'text': brief, 'truncated': bool, 'passages': n_shown}."""
    lines = header(bill, refs)

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
        # The balance is stated because it is checkable and the model cannot
        # reason its way past it. A bill that deletes eleven words is not
        # repealing a protection, however much the added text sounds like one.
        added = sum(len(s.text.split()) for s in diff.insertions)
        removed = sum(len(s.text.split()) for s in diff.deletions)
        lines.append(
            f"\nCHANGED LANGUAGE ({len(keep)} of {len(blocks)} passages). "
            f"{ADD_OPEN}text{ADD_CLOSE} is being ADDED to law; "
            f"{DEL_OPEN}text{DEL_CLOSE} is being DELETED from law. "
            f"Unmarked text is existing law, shown for context, and is not "
            f"changing. In total this bill adds {added:,} words and deletes "
            f"{removed:,}. Line numbers are the Legislature's own.")
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
    """The bill as printed: existing law with additions and deletions both in
    place. The only reading that contains a passage spanning an edit."""
    return " ".join(s.text for s in diff.segments)


def readings(diff: BillDiff) -> list[str]:
    """Every text a faithful quote could have come from.

    An amendatory bill is not one document. Where it substitutes a word the
    page shows the old and the new together, so the sentence a reader would
    quote -- either before the change or after it -- appears in neither the
    page nor each other. All three are the bill.
    """
    return [
        " ".join(s.text for s in diff.segments if s.kind != DELETE),   # as amended
        " ".join(s.text for s in diff.segments if s.kind != INSERT),   # as it stands
        source_text(diff),                                             # as printed
    ]


def chunks(bill: dict, diff: BillDiff, refs: list[dict],
           budget: int = DEFAULT_BUDGET) -> list[dict]:
    """Split a bill into as many briefs as it takes to show all of it.

    `build` keeps the biggest passages and drops the tail, which is right when
    the tail is small and wrong when it is most of the bill: CS/CS/HB 797 has
    57 changed passages and was summarised from one of them. This partitions
    the passages in document order instead, so every one is read -- just not
    all in the same request.

    Each chunk carries the same header, so part four still knows which bill it
    belongs to, and says which part it is, so the model does not describe a
    fragment as though it were the whole act.
    """
    blocks = context_blocks(diff)
    bodies = [(blk, render_passage(blk.parts)) for blk in blocks]

    groups: list[list] = []
    current: list = []
    used = 0
    for blk, body in bodies:
        # A single passage over budget still goes in alone: splitting inside a
        # passage would cut a sentence in half, and half a sentence cannot be
        # quoted.
        if current and used + len(body) > budget:
            groups.append(current)
            current, used = [], 0
        current.append((blk, body))
        used += len(body)
    if current:
        groups.append(current)
    if not groups:
        return [build(bill, diff, refs, budget)]

    out = []
    for i, group in enumerate(groups, 1):
        lines = header(bill, refs)
        lines.append(
            f"\nCHANGED LANGUAGE — PART {i} OF {len(groups)} "
            f"({len(group)} passages of {len(blocks)} in the bill). "
            f"{ADD_OPEN}text{ADD_CLOSE} is being ADDED to law; "
            f"{DEL_OPEN}text{DEL_CLOSE} is being DELETED from law. "
            f"Unmarked text is existing law, shown for context, and is not "
            f"changing. This is one part of a longer bill: describe only what "
            f"is in front of you and do not characterise the act as a whole. "
            f"Line numbers are the Legislature's own.")
        for blk, body in group:
            at = f"line {blk.line}" if blk.line else "—"
            lines.append(f"\n[{at}] {body}")
        out.append({"text": "\n".join(lines), "truncated": False,
                    "passages": len(group), "total_passages": len(blocks),
                    "part": i, "parts": len(groups)})
    return out
