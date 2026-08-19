"""The analysis passes run over one bill.

Each pass is a separate, narrowly-scoped request, which produces better output
than one prompt asked to do everything at once. It is also nearly free after
the first, but only if the prompts are laid out correctly.

**Where the instructions live is a performance decision, not a style one.**
The server reuses a cached prompt prefix only when the part that differs
between requests is very short. Measured on one bill, three passes:

    task text differing by ~3 tokens      pass 2: 1.2s   (99.8% cached)
    task text differing by ~150 tokens    pass 2: 12.5s  (0% cached)

So every instruction lives in the system prompt, which is identical across
passes and therefore cached, and the user turn ends with a three-token
selector naming the pass. The JSON schema differs per pass and costs nothing:
it does not enter the cache key at all.

Get this wrong and each pass pays full prefill -- roughly ten times the cost
for output that is no better.
"""
from __future__ import annotations

import re

from .schema import IMPLICATIONS, PROVISIONS, SUMMARY

SYSTEM = """You explain Florida legislation to people who must act on it: \
legislators voting on the bill, and members of the public petitioning them.

Rules you must follow:
- Use ONLY the bill text provided. If it does not say something, you do not know it.
- Write plain English. No legalese, no hedging, no filler.
- Every quote must be copied EXACTLY from the bill text, with no [[+ +]] or \
[[- -]] markers, and must be a span you can point to.
- [[+text+]] is language being ADDED to Florida law. [[-text-]] is language \
being DELETED from it. What a bill deletes is often more consequential than \
what it adds.
- Never speculate about anyone's motives, party, or intent. Describe what the \
text does and what it permits.

The final line of the message names which task to perform.

TASK summary
Say what actually changes for people in practice, not what the bill is \
"about". If it deletes an existing requirement, protection, or limit, say so \
plainly -- that is usually the most important thing about a bill.

TASK provisions
List the provisions that matter, most important first. Include a provision \
only if it changes what someone may, must, or cannot do. Skip renumbering, \
cross-reference updates and effective-date clauses unless they change \
substance. Prefer four well-chosen provisions to twelve trivial ones. Quote \
the exact language each one rests on.

TASK implications
Identify what this language would permit or require in practice. You are \
describing the reach of the text, not anyone's plans. For each entry, name a \
concrete situation the wording allows, and quote the words that allow it. Pay \
attention to: discretion granted without a standard to meet, terms left \
undefined, removed notice or review requirements, and penalties or duties \
whose scope is broader than the bill's stated subject.

If the text is narrow and its effects are plain, return few entries or none. \
Do not manufacture concerns. Never suggest what any person, party or sponsor \
wants -- only what the words themselves would allow."""

# Kept deliberately tiny: this is the only part that differs between passes,
# and its length is what decides whether the cached prefix is reused.
TASKS = {name: f"TASK {name}" for name in ("summary", "provisions", "implications")}

ORDER = ["summary", "provisions", "implications"]
SCHEMAS = {"summary": SUMMARY, "provisions": PROVISIONS,
           "implications": IMPLICATIONS}
# Length floors in the schema raise the minimum viable completion, so these
# must leave real headroom. Too tight and the model is cut off mid-string,
# which surfaces as unparseable JSON rather than as a truncation.
BUDGET = {"summary": 900, "provisions": 2000, "implications": 2000}

# A deterministic backstop under the prompt. Speculation about motive is the
# one failure this project cannot afford, so it is checked for rather than
# merely discouraged.
INTENT_LANGUAGE = re.compile(
    r"\b(?:the )?(?:sponsor|author|legislator|senator|representative|"
    r"republican|democrat|gop|lawmaker)s?\b|"
    r"\b(?:intend|intends|intended|intention|motive|agenda|really wants?|"
    r"aims? to|designed to allow|in order to let)\b", re.I)


def messages(brief_text: str, pass_name: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM},
        # brief first so the cached prefix is shared across every pass
        {"role": "user", "content": f"{brief_text}\n\n{TASKS[pass_name]}"},
    ]


def flags_intent(text: str) -> str | None:
    m = INTENT_LANGUAGE.search(text or "")
    return m.group(0) if m else None
