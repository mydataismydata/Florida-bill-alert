"""Run the passes over one bill and return only what survives checking."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from ..diff import BillDiff, Segment
from . import passes as P
from .backend import Backend
from .brief import build as build_brief
from .brief import readings
from .verify import Verifier, is_degenerate, verify_claims

# Models return the citation both ways -- "493.6102" and "s. 493.6102" -- and
# a display layer that prepends "s. " turns the second into "s. s. 493.6102".
_CITE_PREFIX = re.compile(r"^\s*(?:ss?\.|section)\s*", re.I)


# The model likes to annotate the field -- "493.6102(16) (new) - 28 words
# added, 0 deleted" -- and a maxLength generous enough not to truncate a real
# citation is generous enough to let that through. A citation is a number,
# optional subsections, and nothing else.
_CITE_ONLY = re.compile(r"^\s*\d+[A-Za-z]?\.[\w.]*\d[\w.]*(?:\([^)]{1,8}\))*")


def tidy_statute(cite: str) -> str:
    bare = _CITE_PREFIX.sub("", cite or "").strip()
    m = _CITE_ONLY.match(bare)
    return (m.group(0) if m else bare).strip().rstrip(".")

KIND_NAMES = {0: "plain", 1: "insert", 2: "delete"}

# Brief scale per attempt. Temperature is not a lever here: the server decodes
# greedily and ignores it, so what varies between attempts is the brief and the
# note telling the model why the last answer was rejected.
RETRIES = (1.0, 1.0, 0.4)


def load_bill(db, session: str, num: int):
    """Assemble everything the analysis needs from the deterministic layer."""
    bill = db.execute("SELECT * FROM bill WHERE session=? AND num=?",
                      (session, num)).fetchone()
    if bill is None:
        return None
    render = db.execute("SELECT version,fmt,segments FROM bill_render"
                        " WHERE session=? AND num=?", (session, num)).fetchone()
    if render is None:
        return None
    segs = [Segment(KIND_NAMES[k], t, ln, pg)
            for k, ln, pg, t in json.loads(render["segments"])]
    refs = [dict(r) for r in db.execute(
        "SELECT * FROM statute_ref WHERE session=? AND num=?"
        " ORDER BY bill_section IS NULL, bill_section", (session, num))]
    return dict(bill), render["version"], BillDiff(render["fmt"], segs), refs


def summary_prose(data: dict) -> str:
    """The summary pass's prose as one string, for checking."""
    body = data.get("summary") or []
    parts = [data.get("one_line") or ""]
    parts += body if isinstance(body, list) else [str(body)]
    return " ".join(p for p in parts if p)


@dataclass
class Analysis:
    session: str
    num: int
    label: str
    version: str
    model: str
    summary: dict = field(default_factory=dict)
    provisions: list = field(default_factory=list)
    implications: list = field(default_factory=list)
    unclear: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    flagged: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    reading: str = ""
    stats: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def analyze(db, session: str, num: int, backend: Backend,
            budget: int = 24_000, log=lambda *_: None) -> Analysis | None:
    loaded = load_bill(db, session, num)
    if loaded is None:
        return None
    bill, version, diff, refs = loaded

    brief = build_brief(bill, diff, refs, budget=budget)
    verifier = Verifier(readings(diff))
    # What the bill actually strikes, for checking claims of repeal against.
    deleted_text = " ".join(s.text for s in diff.deletions)
    out = Analysis(session=session, num=num, label=bill.get("label", ""),
                   version=version, model=backend.model)
    t0 = time.time()

    for name in P.ORDER:
        data, reply = None, None
        # Two levers, because the faults have two shapes. A shorter brief
        # recovers a pass that degenerates on the full one. Temperature
        # recovers a pass that produced clean JSON saying the wrong thing --
        # at temperature 0 a retry on the same brief is byte-identical, so
        # without this the ladder silently re-asks and re-fails.
        note = ""
        for attempt, shrink in enumerate(RETRIES):
            text = (brief["text"] if shrink == 1.0 else
                    build_brief(bill, diff, refs,
                                budget=int(budget * shrink))["text"])
            reply = backend.chat(P.messages(text, name, note),
                                 schema=P.SCHEMAS[name],
                                 max_tokens=P.BUDGET[name])
            why = is_degenerate(reply.text)
            if why is None:
                try:
                    parsed = reply.json()
                except json.JSONDecodeError:
                    why = "unparseable JSON"
                else:
                    # A summary that reverses the bill is worse than no
                    # summary at all, so a contradicted one is re-rolled and
                    # then abandoned rather than published.
                    why = (P.flags_false_repeal(summary_prose(parsed),
                                                deleted_text)
                           or P.flags_cut_headline(parsed.get("one_line", ""))
                           if name == "summary" else None)
                    if why is None:
                        data = parsed
                        break
            out.failures.append({"pass": name, "attempt": attempt + 1,
                                 "reason": why, "brief_scale": shrink})
            note = P.retry_note(why)
            log(f"    {name}: attempt {attempt + 1} unusable ({why})")

        if data is None:
            log(f"    {name}: giving up after retry")
            continue
        log(f"    {name}: {reply.seconds:.1f}s "
            f"({'cached' if reply.cache_hit else 'full prefill'})")

        if name == "summary":
            out.summary = data
        elif name == "provisions":
            kept, dropped = verify_claims(data.get("provisions", []), verifier)
            for claim in kept:
                claim["statute"] = tidy_statute(claim.get("statute", ""))
            out.provisions = kept
            out.dropped += [dict(d, pass_name=name) for d in dropped]
        elif name == "implications":
            kept, dropped = verify_claims(data.get("implications", []), verifier)
            # a claim about motive is removed even when its quote is genuine
            clean = []
            for claim in kept:
                hit = (P.flags_intent(claim.get("consequence", ""))
                       or P.flags_inverted_prohibition(
                           claim.get("consequence", ""), claim.get("quote", "")))
                if hit:
                    out.flagged.append(dict(claim, flagged_for=hit))
                else:
                    clean.append(claim)
            out.implications = clean
            out.reading = data.get("reading", "")
            out.unclear = data.get("unclear", [])
            out.dropped += [dict(d, pass_name=name) for d in dropped]

    total = len(out.provisions) + len(out.implications) + len(out.dropped)
    out.stats = {
        "seconds": round(time.time() - t0, 1),
        "brief_chars": len(brief["text"]),
        "passages_shown": brief["passages"],
        "passages_total": brief["total_passages"],
        "truncated": brief["truncated"],
        "claims_kept": len(out.provisions) + len(out.implications),
        "claims_dropped": len(out.dropped),
        "claims_flagged": len(out.flagged),
        "pass_failures": len(out.failures),
        "verify_rate": round(1 - len(out.dropped) / total, 3) if total else None,
    }
    return out
