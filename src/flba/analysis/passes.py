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

from .schema import CHECK, IMPLICATIONS, ONE_LINE_MAX, PROVISIONS, SUMMARY

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
- Words inside [[- -]] stop being law when the bill passes. Never describe \
struck words as something the law still allows or requires.
- Many marked edits are punctuation, renumbering or cross-reference updates. \
A deleted comma is not a repeal, and a semicolon replacing a comma changes \
nothing at all -- least of all the clause that happens to follow it. Judge an \
edit by the words it changes.
- Never speculate about anyone's motives, party, or intent. Describe what the \
text does and what it permits.

How to write. These rules apply to every prose field in every task:
- One idea per sentence, and at most 25 words in any sentence. If a sentence \
needs a dash, a semicolon or a second "and" to carry another idea, write two \
sentences.
- Active voice. Name who acts, then what they must, may or cannot do: "The \
county must hold a hearing", not "A hearing must be held".
- Start with the point. Never open a sentence or paragraph with a label such \
as "The biggest change:", "Second:", "Finally,", "Note:" or "Practical effect:".
- Write only your conclusions. Never show your working: no "Hmm", no \
"Actually", no "Let me", and no questions to yourself about the text.
- Use your own plain words. The bill's exact language belongs in the quote \
field, not in the summary, the effect or the consequence.
- Shorter must never mean looser. Keep each legal term exactly as the bill \
uses it ("may not", "shall", "quasi-judicial"). Keep every limiting word -- \
"only", "further", "unless", "except", "not". Dropping one changes what the \
clause does.

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

Before saying what a deletion does, decide what kind of clause was struck -- \
the kind decides the direction, and getting it wrong states the exact \
opposite of what the bill does:
- A GRANT OF AUTHORITY struck ("may apply for ...") means that route is gone.
- A RESTRICTIVE QUALIFIER struck ("of a 501(c)(3) organization") means scope \
WIDENS to reach more than before -- not less.
- A struck word that narrows WHO a duty falls on ("each [[-county-]] \
hospital shall report") widens the duty to everyone the noun still names: \
every hospital must now report, county hospitals included. Striking the word \
releases no one from the duty.
- A struck NAME in a list of who must act ("each hospital [[-and clinic-]] \
shall report") does not show that group is free of the duty. A word the bill \
keeps, such as "hospital", "utility" or "provider", may still cover it through \
a definition the bill does not show. Say whom the duty now names, in the \
bill's words, and never say the struck group no longer has it.
- A PRECONDITION struck ("after assurances have been provided") means the \
condition is gone, not that the underlying duty vanished.
- A CEILING struck ("may not exceed $1,500") means the limit is lifted; \
whatever duty sits under it is unchanged. A ceiling written as "may not \
exceed" or "shall not exceed" is not a prohibition on the underlying act: \
"compensation may not exceed 125 percent of the Medicare rate" permits \
everything under that cap, and striking the clause raises or removes the \
cap -- it does not forbid compensation.

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

The style, shown on an invented bill. Copy its shape and length, never its \
facts:
  one_line: Lets pharmacists renew a maintenance prescription without a new order.
  summary:
  - A pharmacist may renew a maintenance prescription once, for up to 90 days, \
without contacting the prescriber. The patient must have filled it for at \
least six months.
  - The renewal is not allowed for controlled substances. The pharmacist must \
tell the prescriber about each renewal within 7 days.

TASK provisions
List the provisions that matter, most important first. Include a provision \
only if it changes what someone may, must, or cannot do. Skip renumbering, \
cross-reference updates and effective-date clauses unless they change \
substance. Prefer four well-chosen provisions to twelve trivial ones. Quote \
the exact language each one rests on. Write each heading in under 10 words \
and each effect in one or two short sentences.

TASK implications
Identify what this language would permit or require in practice. You are \
describing the reach of the text, not anyone's plans. For each entry, name a \
concrete situation the wording allows, in one or two short sentences, and \
quote the words that allow it. Pay \
attention to: discretion granted without a standard to meet, terms left \
undefined, removed notice or review requirements, and penalties or duties \
whose scope is broader than the bill's stated subject.

If the text is narrow and its effects are plain, return few entries or none. \
Do not manufacture concerns. Never suggest what any person, party or sponsor \
wants -- only what the words themselves would allow.

TASK check
The message ends with a DRAFT someone wrote about this bill, as numbered \
sentences. Check each sentence against the bill text, one at a time. Return \
one entry for every numbered sentence, in order.

Mark a sentence "wrong" only when the bill text shows that it is false. Look \
for these mistakes:
- It says the law still allows or requires something whose words sit inside \
[[- -]].
- It says a group no longer has a duty because the bill strikes the group's \
name, or a word that narrowed who had the duty. If the bill still gives the \
duty to a broader word, such as "utility" or "provider", that word may still \
cover the group.
- It reverses who may, must or cannot act, or reads a prohibition as a \
permission.
- It gets a number, date, deadline or amount wrong.
- It says the opposite of the bill in any other way.
A sentence that leaves something out, could be worded better, or stresses a \
different point is NOT wrong. Mark it "agrees".

For a wrong sentence, say what is wrong in one sentence. Quote the exact bill \
words that show it, copied without the markers. Then write a corrected \
sentence in the same short, plain style, and keep everything in it that was \
right. A corrected headline must still be a 6-to-12-word verb phrase. If the \
sentence cannot be put right, leave the fix empty and it will be removed. For \
a sentence that agrees, leave problem, quote and fix empty."""

# Kept deliberately tiny: this is the only part that differs between passes,
# and its length is what decides whether the cached prefix is reused.
TASKS = {name: f"TASK {name}"
         for name in ("summary", "provisions", "implications", "check")}

ORDER = ["summary", "provisions", "implications"]
SCHEMAS = {"summary": SUMMARY, "provisions": PROVISIONS,
           "implications": IMPLICATIONS, "check": CHECK}
# Length floors in the schema raise the minimum viable completion, so these
# must leave real headroom. Too tight and the model is cut off mid-string,
# which surfaces as unparseable JSON rather than as a truncation.
# Doubled with the schema caps for the GLM 5.3 Flash test; the old values
# were 900, 2000 and 2000.
# The check returns an entry for every sentence, most of them empty, so its
# budget follows the length of the draft rather than of the bill.
BUDGET = {"summary": 1800, "provisions": 4000, "implications": 4000,
          "check": 4000}

# How many times the check re-reads a draft it has just corrected. A
# correction is written by the model with the bill in view and the fault
# named, but it is still new prose, so it is read again once.
CHECK_ROUNDS = 2

# What a single request could have returned. A bill read in parts must
# not publish more just for being long -- the reader's attention did not
# grow with the page count.
PROVISION_CAP = 8
IMPLICATION_CAP = 6

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

# A pronoun and a wanting-verb used to count as attributing motive. Over 187
# bills that clause fired twice and was wrong both times: "as many times as
# they want" is an idiom about applicants, and "patrons who intend to leave
# with it" is a legal category the bill defines. Motive attribution needs a
# political actor named, which the list above already requires.
INTENT_LANGUAGE = re.compile(
    # naming a political actor at all, in what should be textual analysis
    rf"\b(?:{ACTORS})s?\b|{TITLED}"
    r"|\b(?:real|true|hidden|underlying)\s+(?:motive|intent|intention|purpose|agenda)\b"
    r"|\b(?:motivated by|pretext|smokescreen)\b",
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


# A quote that fails the check used to end the claim, and over 703 bills that
# discarded 335 of them. The check is strict on purpose, but it fires on
# claims that are otherwise sound: the model opens the span correctly and then
# drifts, or stitches across a gap. Naming the rejected quote is the only
# thing that makes the second ask a different question -- greedy decoding
# returns the same bytes for the same input.
#
# Two, because each retry loses the cached prefix and pays full prefill.
QUOTE_RETRY_LIMIT = 2

# Enough failures to show the model the pattern, few enough to stay short.
_QUOTES_SHOWN = 4


def quote_retry_note(dropped: list[dict], field: str = "quote") -> str:
    """Tell the model which quotes were not in the bill, and what to do next."""
    lines = []
    for claim in dropped[:_QUOTES_SHOWN]:
        quote = " ".join((claim.get(field) or "").split())
        if len(quote) > 90:
            quote = quote[:90] + "..."
        reason = claim.get("drop_reason", "not found in the bill text")
        lines.append(f'  - "{quote}" -- {reason}')
    hidden = len(dropped) - _QUOTES_SHOWN
    if hidden > 0:
        lines.append(f"  - and {hidden} more that also failed the check")
    return (
        "These quotes from your previous answer are not in the bill:\n"
        + "\n".join(lines)
        + "\n\nEvery quote must be copied character for character from the "
        "bill text above. Prefer one short clause, under 180 characters. Do "
        "not join two passages, do not paraphrase, and do not repair a quote "
        "by guessing at the missing words. Return replacements only for the "
        "claims listed here. If a claim has no genuine supporting quote in "
        "the bill, leave that claim out."
    )


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


# Every other string the decoder closes at its limit is kept, so it has to stop
# somewhere a reader can stop. SB 686's definition provision came back
# "...raises the size cap to 4,480 acres in water-pl" at exactly 500
# characters. Trimmed, that reads "...raises the size cap to 4,480 acres…".
#
# Prose keeps its last full sentence when that holds at least half the text;
# otherwise it loses the half-written word and any small word left dangling
# ("in", "of", "and"), and gains an ellipsis. The ellipsis matters: what was
# cut off is often a qualifier -- here "in water-plan counties" -- and without
# it the trimmed claim would read broader than the one the model wrote.
#
# Quotes are left to verify.verify_claims, which trims them against the bill
# itself: from the text alone, "...or public hearing" cut at the limit looks
# exactly like a quote cut inside "hearings". The headline is left alone too,
# because flags_cut_headline re-rolls it and needs to see the cut.
_CUT_SLACK = 0.97          # decoders stop a token short of the limit, not on it
_SENTENCE_END = re.compile(r"[.!?][\"'”’)]?(?=\s|$)")
# "s. 384.30" is not the end of a sentence, and statute text is full of them.
_ABBREV = frozenset("""s ss e.g i.e u.s u.s.c fla stat no nos st inc co corp jr
    sr dr mr ms mrs vs v ch art sec para subpara approx""".split())
_DANGLING = frozenset("""
    a an the and or but nor of in on at to for from by with as into onto than
    that which who whose whom if unless when where while under over between
    within without via per its their his her such any each every no not may
    must shall will can is are was were be been being has have had including
    plus both either neither so""".split())


def _was_cut(text: str, limit: int) -> bool:
    end = text.rstrip()
    return (len(end) >= limit * _CUT_SLACK
            and not (end.endswith((".", "!", "?")) and not _CUT_TAIL.search(end)))


def _sentence_ends(text: str):
    for m in _SENTENCE_END.finditer(text):
        if text[m.start()] == ".":
            prev = re.search(r"([\w.]+)$", text[:m.start()])
            if prev and (prev.group(1).lower() in _ABBREV or len(prev.group(1)) == 1):
                continue
        yield m.end()


def _trim_words(text: str) -> str:
    words = text.rstrip().split()
    # The last word is half-written unless punctuation shows it was finished.
    if words and not re.search(r"[,;:)\]]$", words[-1]):
        words.pop()
    while words and re.sub(r"[^\w]", "", words[-1]).lower() in _DANGLING | {""}:
        words.pop()
    return re.sub(r"[\s,;:\-‐-―(]+$", "", " ".join(words))


def trim_cut(value, schema: dict, field: str = ""):
    """Trim every string the decoder cut at its maxLength back to a stopping point."""
    kind = schema.get("type")
    if kind == "object" and isinstance(value, dict):
        props = schema.get("properties", {})
        return {k: trim_cut(v, props[k], k) if k in props else v
                for k, v in value.items()}
    if kind == "array" and isinstance(value, list):
        return [trim_cut(v, schema.get("items", {}), field) for v in value]
    limit = schema.get("maxLength")
    if (kind != "string" or not isinstance(value, str) or not limit
            or field in ("one_line", "quote") or not _was_cut(value, limit)):
        return value

    floor = max(schema.get("minLength", 0), 1)
    ends = list(_sentence_ends(value))
    if ends and ends[-1] >= len(value) / 2:
        return value[:ends[-1]]
    out = _trim_words(value)
    return out + "…" if len(out) >= floor else value


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


# "Compensation may not exceed 125 percent of the Medicare rate" is a ceiling,
# and a ceiling genuinely permits everything under it -- so "a hospital may
# bill up to 125 percent" is a correct reading, not an inverted one. Six of
# the seventeen claims this guard removed were of exactly that shape,
# including one whose own wording said "the MAXIMUM number of terms".
CEILING = re.compile(
    r"\b(?:may|shall|must|can)\s+not\s+[^.;]{0,48}?"
    r"(?:exceed|more than|greater than|less than|longer than)\b"
    r"|\bnot to exceed\b|\bno more than\b|\bnot (?:more|less) than\b",
    re.I)


def flags_inverted_prohibition(consequence: str, quote: str) -> str | None:
    """Catch a claim that reads a prohibition as though it granted permission."""
    if not PROHIBITION.search(quote or ""):
        return None
    if CEILING.search(quote or ""):
        return None
    if NEGATED.search(consequence or ""):
        return None
    m = PERMISSION.search(consequence or "")
    if m:
        return f"reads a prohibition as permission ({m.group(0)!r})"
    return None
