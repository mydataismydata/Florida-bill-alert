# Analysis notes — the AI layer

What the model is asked, what it is allowed to return, and what is thrown away
before publication. Measured on an M5 Max (64 GB) running
`mlx-community/Qwen3.8-27B-4bit` through `mlx_vlm.server`.

## Shape

Three narrowly-scoped passes per bill — summary, key provisions, and what the
language would permit — against an OpenAI-compatible endpoint, so the same
client serves the local MLX server, Ollama, or a hosted API.

The model does **not** read raw bill text. It reads a brief assembled from the
deterministic layer: which statutes are touched and how, at which lines, then
the changed language itself with `[[+additions+]]` and `[[-deletions-]]`
marked. An amendatory bill's meaning is in what it removes from existing law,
and that is already extracted, so the model spends its context on substance
and every fact it is shown is one a reader can check.

Median brief is 4,937 characters. **8.7% of bills (165) overflow the budget**
and are truncated — see *Known gap* below.

## Every claim is checked

A claim must quote the bill verbatim, and the quote is checked against the
source before publication. Paraphrase, conflation and invention all fail;
failures are dropped rather than softened, and recorded so the rate stays
visible. Typography and marker differences are tolerated, meaning is not.

A second, deterministic guard removes claims that speculate about motive, even
when their quote is genuine. Describing what text permits is checkable;
guessing at intent is not, and in front of this audience it would be
indefensible.

That guard has to be precise as well as strict. Its first version discarded a
sound claim because the bill described "a rural study area **intended** for
residential development" — a land use designation, not a motive. It now
requires the sentence to point at a person or party.

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

Throughput on this machine: prefill 625–870 tok/s, generation 24–37 tok/s.
Generation dominates, so output length is the thing to bound.

## A grammar guarantees well-formed output, not useful output

Schema-forced decoding makes invalid JSON impossible. It also makes the
**shortest valid completion the cheapest one**, and a model that is unsure will
take that exit. Three real failures, each now guarded:

**An empty array is always legal.** Asked for implications on a bill repealing
an entire category of local taxation, the model returned `[]`. The fix is to
require a written reading of the bill *before* the list — prose first, so the
model has reasoned before it reaches the array. `minItems` was tried first and
is worse: it forces invention on bills that genuinely have little to say, and
produced quotes scraped from the bill *title*.

**Length floors create their own trap.** Cornered by a floor it could not
satisfy, the model emitted `}` as the first character of a string and then
padded with newlines until the token budget ran out — well-formed, on-schema,
and worthless. Padding runs and unclosed objects are now detected and the pass
retried against a shorter brief, which recovers it. Temperature does not help;
this is not a sampling accident.

**"Two to four sentences" yields one 200-word paragraph.** Technically
compliant, unreadable. The summary field is now an *array*: separate items
render as separate paragraphs and force one idea each.

## Scope is arithmetic, not analysis

Bills state their reach by population band — "a county with a population of
1.75 million or less" — which is precise and unreadable. Resolving the band
against the population series the Legislature itself uses turns it into a list
of counties. For CS/CS/CS/SB 686 that band means **65 of 67 counties: every one
except Broward and Miami-Dade.**

This sits with the mechanically-extracted sections, not inside the analysis
box, because the threshold is in the bill and the arithmetic is the same for
anyone who does it. County figures come from the BEBR estimates prepared under
contract with the Legislature, carried in the repository with provenance.

Naming a statutory category is not the same as being limited to one: SB 686
names "areas of critical state concern" in order to **exclude** that land, and
reporting it as a limit inverted the meaning. A category now counts only where
the same sentence carries limiting language.

## Known gap

**Large bills are truncated, and it shows.** For heavily-amended bills the
brief cannot hold every changed passage, and the model says so itself — on one
it wrote *"The provided text is a fragment of a larger bill."* It was right,
and its output for that bill was correspondingly weak.

The fix is map-reduce over the bill's own sections, which the corpus supports:
**11,547 sections, median 1,042 characters, 97.6% under 3,000 tokens.** Each
section maps to one statute, so it is a natural unit of meaning. Not built yet.

## What verification actually catches

Across the first eleven bills analysed, **14 of 98 claims were discarded
because their quote could not be found in the bill** — a 85.7% verification
rate. Spot-checking the failures showed they are real, not pedantry:

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

## Not yet evaluated

Eleven bills is enough to measure the verification rate and not much else.
Output also varies between runs more than temperature 0 suggests, because a
schema change alters the token stream.

The corpus already contains ground truth for a real evaluation: **890 bills
carry committee staff analyses written by people who read the same text.**
Until that comparison exists, treat the quality claims here as unproven.
