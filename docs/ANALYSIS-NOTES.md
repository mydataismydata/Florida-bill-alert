# Analysis notes — the AI layer

What the model is asked, what it is allowed to return, and what is thrown away
before publication.

## Shape

The analysis runs three narrowly-scoped passes per bill: summary, key provisions, and what the
language would permit. This is run against an OpenAI-compatible endpoint, so the same
client serves the local MLX server, Ollama, or a hosted API.

The model does **not** read raw bill text. It reads a brief assembled from the
deterministic layer: which statutes are touched and how, at which lines, then
the changed language itself with `[[+additions+]]` and `[[-deletions-]]`
marked. An amendatory bill's meaning is in what it removes from existing law,
and that is already extracted, so the model spends its context on substance
and every fact it is shown is one a reader can check.

## Every claim is checked

A claim must quote the bill verbatim, and the quote is checked against the
source before publication. Paraphrase, conflation and invention all fail;
failures are dropped rather than softened, and recorded so the rate stays
visible.

A second, deterministic guard removes claims that speculate about motive, even
when their quote is genuine. Describing what text permits is checkable;
guessing at intent is not.

## Prompt layout is a performance decision

The server reuses a cached prompt prefix only when the part that differs
between requests is very short. On one bill, three passes:

| where the task text lives | pass 2 |
|---|---|
| user turn, ~3 tokens of divergence | **1.2s** (99.8% cached) |
| user turn, ~150 tokens of divergence | 12.5s (0% cached) |

So every instruction lives in the system prompt, which is identical across
passes, and the user turn ends with a three-token selector naming the pass.
**The JSON schema differs per pass and costs nothing — it does not enter the
cache key.** Get this wrong and each pass pays full prefill, roughly ten times
the cost, for output that is no better.

## Extracting well-formed output

**An empty array is always legal.** Asked for implications on a bill repealing
an entire category of local taxation, the model returned `[]`. The fix is to
require a written reading of the bill *before* the list, prose first, so the
model has reasoned before it reaches the array.

**"Two to four sentences" yields one 200-word paragraph.** Technically
compliant, but unreadable. The summary field is now an *array*: separate items
render as separate paragraphs and force one idea each.

## What verification actually catches

Across the first eleven bills analysed, **14 of 98 claims were discarded
because their quote could not be found in the bill** — a 85.7% verification
rate. Spot-checking the failures showed they are real:

- One quote — *"within 500 feet of a place where children were congregating"* —
  matched **zero characters** of the bill. Entirely invented.
- Most others opened correctly and then drifted mid-quote, the model stitching
  across a gap or paraphrasing the tail.

So roughly one claim in seven would otherwise have been published with
fabricated supporting evidence. This single check does more for credibility
than any amount of prompt wording.

The failures were nearly all *long* quotes, so quotes are now capped at 180
characters with instructions to prefer a short exact span. That alone moved
the rate from 81.4% to 85.7%, and on the four worst bills from 67.6% to 80.0%.

**Verification rate is the metric to watch as this scales** — not how well any
single bill reads.
