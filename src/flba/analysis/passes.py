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

from .schema import IMPLICATIONS, ONE_LINE_MAX, PROVISIONS, SUMMARY

SYSTEM = """You explain Florida legislation to people who must act on it: \
legislators voting on the bill, and members of the public petitioning them.

Rules you must follow:
- Use ONLY the bill text provided. If it does not say something, you do not know it.
- Write plain English. No legalese, no hedging, no filler.
- Write SHORT. One idea per sentence, one point per paragraph. A reader is \
scanning this between meetings, not studying it. Long dense paragraphs are a \
failure even when every word is accurate.
- Every quote must be copied EXACTLY from the bill text, with no [[+ +]] or \
[[- -]] markers, and must be a span you can point to.
- Keep quotes SHORT -- 6 to 25 words of contiguous text. Never join two \
passages into one quote and never drop words from the middle. A short exact \
quote is worth more than a long approximate one; a quote that is not \
word-for-word will be discarded and your claim lost with it.
- [[+text+]] is language being ADDED to Florida law. [[-text-]] is language \
being DELETED from it. Unmarked text is existing law, shown for context, and \
is NOT changing.
- Read direction off the markers, never off the words inside them. A \
prohibition inside [[+ +]] is a NEW restriction the bill creates; those same \
words inside [[- -]] would be an existing restriction it repeals. Calling \
added language a removal states the exact opposite of what the bill does.
- Deletions are easy to miss and often matter, so say plainly when a \
requirement, protection or limit is being repealed -- but only when those \
words sit inside [[- -]]. A bill that deletes nothing of substance repeals \
nothing. Do not reach for a repeal that is not there.
- Many marked edits are punctuation, renumbering or cross-reference updates. \
A deleted comma is not a repeal, and a semicolon replacing a comma changes \
nothing at all -- least of all the clause that happens to follow it. Judge an \
edit by the words it changes.
- Never speculate about anyone's motives, party, or intent. Describe what the \
text does and what it permits.

How Florida drafts. These words are terms of art and do not carry their \
ordinary-English force:
- "MAY NOT" and "SHALL NOT" are BOTH mandatory prohibitions, equal in effect. \
"May not" is the form Florida prefers and is far more common in the statutes. \
It NEVER means "is permitted not to", and a prohibition is not weaker for \
using it. If the text says an assessment "may not be levied", then levying it \
is forbidden.
- "SHALL" and "MUST" both impose a mandatory duty.
- "MAY" on its own is permissive and grants discretion.
- "IS ENTITLED TO" confers a right.
Never argue that a prohibition is weak, optional or merely advisory because of \
which of these words it uses.

The final line of the message names which task to perform.

TASK summary
Say what actually changes for people in practice, not what the bill is \
"about". Lead with whichever change reaches the most people or hits them \
hardest, whether that change creates a duty or removes one.

The one-line field must be a SHORT verb phrase naming the single most \
important effect -- 6 to 12 words, starting with a verb, ending in a full \
stop. The reader already knows it is a bill, so never write "This bill ...", \
and never preview what the paragraphs below will say. Cut every qualifier that \
is not load-bearing. Name the effect, not the machinery: say what becomes \
possible or forbidden, never "replaces X with Y" or "changes the process \
for Z". A hard limit cuts this field off mid-word, so if it will not fit in \
twelve words you have written the wrong sentence -- write a shorter one.

Then give two to four SHORT paragraphs of 25-45 words, one point each. Do not \
write a single long block.

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
#
# Precision matters as much as recall here. Legal drafting is full of innocent
# intent words -- "a rural study area intended for residential development"
# describes a land use designation, not a motive -- and an over-eager guard
# discards verified claims. So bare intent verbs are not enough: the sentence
# has to point at a person or party.
# "Senator" and "Representative" are titles in legislative usage and ordinary
# nouns everywhere else -- a bill about unclaimed property speaks of
# "claimants' representatives", meaning agents, not members of the House. So
# those two only count as a title immediately before a name.
ACTORS = (r"sponsor|author|lawmaker|legislator|"
          r"republican|democrat|gop|the bill'?s backers?|proponents?|"
          r"opponents?")
# Case-sensitive on purpose: the surrounding pattern is case-insensitive, so
# without the scoped flag this matches "representative may" as readily as
# "Representative Smith".
TITLED = r"(?-i:\b(?:Senator|Representative|Rep\.|Sen\.)\s+[A-Z])"

INTENT_LANGUAGE = re.compile(
    # naming a political actor at all, in what should be textual analysis
    rf"\b(?:{ACTORS})s?\b|{TITLED}"
    # or attributing a want to someone
    r"|\b(?:who|they|he|she|which)\s+(?:really\s+)?"
    r"(?:want|wants|wanted|intend|intends|intended|hope|hopes)\b"
    r"|\b(?:real|true|hidden|underlying)\s+(?:motive|intent|intention|purpose|agenda)\b"
    r"|\b(?:motivated by|agenda|pretext|smokescreen)\b",
    re.I)

# Retrying is only useful if something about the request changes. This server
# decodes greedily and ignores temperature, top_p and seed alike -- measured,
# not assumed -- so re-asking the same question returns the same bytes, and a
# retry ladder built on sampling never varied anything. What can change is the
# input, so a retry says what was wrong with the last answer.
#
# This costs the cached prefix: the divergent suffix stops being three tokens
# and the retry pays full prefill. Retries are rare enough that correctness is
# worth more than the second or two.
RETRY_NOTES = (
    ("cut at the", "Your previous answer's one-line field ran past the hard "
                   "character limit and was cut off mid-word. Write a much "
                   "shorter one: six to eight words, ending in a full stop. "
                   "Name the single effect and nothing else."),
    ("claims a repeal", "Your previous answer said the bill repeals or removes "
                        "something. Only text inside [[- -]] is being deleted. "
                        "Check those markers: if the bill deletes nothing of "
                        "substance, say what it creates or requires instead."),
    ("reads a prohibition", "Your previous answer read a prohibition as though "
                            "it granted permission. \"May not\" and \"shall "
                            "not\" both forbid."),
)
_MALFORMED = ("Your previous answer was malformed or ran out of room. Return "
              "complete, valid JSON, and keep every field short.")


def retry_note(reason: str) -> str:
    """What to tell the model about the answer that was rejected."""
    for key, note in RETRY_NOTES:
        if key in (reason or ""):
            return note
    return _MALFORMED


def messages(brief_text: str, pass_name: str, note: str = "") -> list[dict]:
    task = TASKS[pass_name]
    if note:
        task = f"{task}\n\n{note}"
    return [
        {"role": "system", "content": SYSTEM},
        # brief first so the cached prefix is shared across every pass
        {"role": "user", "content": f"{brief_text}\n\n{task}"},
    ]


def flags_intent(text: str) -> str | None:
    m = INTENT_LANGUAGE.search(text or "")
    return m.group(0) if m else None


# A prohibition read as a permission inverts the bill. Florida writes
# prohibitions as "may not" far more often than "shall not" -- 866 occurrences
# against 227 in a sample of the 2026 session -- and a model that treats "may
# not" as the weaker form gets the meaning exactly backwards. The prompt says
# so plainly; this catches it when the prompt does not hold.
PROHIBITION = re.compile(
    r"\b(?:may not|shall not|must not|is prohibited|are prohibited|"
    r"no \w+ may|may no longer)\b", re.I)

# Asserting a capability ...
PERMISSION = re.compile(
    r"\b(?:can|could|is able to|are able to|is allowed to|are allowed to|"
    r"is permitted to|are permitted to|may)\s+\w+", re.I)

# ... unless the sentence is itself negating that capability.
NEGATED = re.compile(
    r"\b(?:cannot|can no longer|can't|may not|is not|are not|no longer|"
    r"not permitted|not allowed|prohibited|barred|prevented|unable)\b", re.I)


# Constrained decoding closes a string the moment it hits maxLength, wherever
# the model happens to be -- so a headline at the cap was cut, not written.
# SB 686 came back "...direct development approval for agricultural", missing
# the noun. Re-rolling is cheap and usually lands shorter.
# "...promote related elected co-." is 95 characters and ends in a full stop,
# so a plain "does it end in punctuation" test called it finished. The word it
# was cutting in half is the tell.
_CUT_TAIL = re.compile(r"[-\u2010-\u2015]\s*[.!?]?$")


def flags_cut_headline(one_line: str) -> str | None:
    line = (one_line or "").rstrip()
    if len(line) < ONE_LINE_MAX - 2:
        return None
    if line.endswith((".", "!", "?")) and not _CUT_TAIL.search(line):
        return None
    return f"headline cut at the {ONE_LINE_MAX}-character limit ({line[-24:]!r})"


# The summary pass carries no quotes, so the quote verifier cannot reach it.
# What can still be checked is direction. A bill cannot repeal a prohibition it
# never deletes -- SB 182 added three prohibitions, deleted none, and came back
# summarised as removing an "academic dismissal ban", a claim its own diff
# refutes outright.
#
# Strict on purpose. Every guard in this project has failed by being too eager,
# so this one fires only when the deleted text contains no matching language
# whatsoever. A bill that strikes any duty may be described as removing a
# requirement; only a bill that strikes nothing of the kind is contradicted.
REPEAL_CLAIM = re.compile(
    r"\b(?:remov\w+|repeal\w+|eliminat\w+|strip\w+|rescind\w+|revok\w+|"
    r"delet\w+|abolish\w+|lifts?|lifted|lifting|does away with)\b"
    r"[^.;]{0,70}?"
    r"\b(?P<what>bans?|prohibitions?|protections?|restrictions?|bar|safeguards?|"
    r"requirements?|mandates?|duty|duties|obligations?)\b",
    re.I)

# Any struck word at all. Nothing more clever: matching the claim's noun
# against a vocabulary of struck language was wrong four times in five.
#
# SB 7004 strikes "shall stand repealed on October 2, 2026" and the model
# correctly called that removing a deadline -- rejected, because the noun it
# named was "protection" and no prohibition was struck. HB 399 eliminates
# local discretion by ADDING a preemption, which is a real removal achieved
# without deleting anything; also rejected. A bill's effect can remove
# something its markup only adds, and no regex over the claim's wording is
# going to know the difference.
#
# What a diff can flatly contradict is narrower: a bill that strikes no words
# at all repeals nothing. SB 572 deletes two commas and was summarised as
# removing a residence requirement. That is the case this catches, and the
# judgement calls are left to the reader.
_STRUCK_WORD = re.compile(r"[A-Za-z]{2,}")


def flags_false_repeal(text: str, deleted: str) -> str | None:
    """Catch a claim of repeal against a bill that strikes no words."""
    if _STRUCK_WORD.search(deleted or ""):
        return None
    for m in REPEAL_CLAIM.finditer(text or ""):
        claim = " ".join(m.group(0).split())
        return (f"claims a repeal ({claim!r}) but the bill strikes no words "
                f"at all")
    return None


def flags_inverted_prohibition(consequence: str, quote: str) -> str | None:
    """Catch a claim that reads a prohibition as though it granted permission."""
    if not PROHIBITION.search(quote or ""):
        return None
    if NEGATED.search(consequence or ""):
        return None
    m = PERMISSION.search(consequence or "")
    if m:
        return f"reads a prohibition as permission ({m.group(0)!r})"
    return None
