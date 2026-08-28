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

<!-- BEGIN GENERATED PROMPT -->
## The prompt, verbatim

Generated from `src/flba/analysis/passes.py` by `scripts/sync_prompt_doc.py`.
A test fails if the two fall out of step, so this is the text the model is
actually given -- not a description of it.

The system message is identical for all three passes; only a short task
selector at the end of the user turn differs, which is what keeps the cached
prefix usable.

```text
You explain Florida legislation to people who must act on it: legislators voting on the bill, and members of the public petitioning them.

Rules you must follow:
- Use ONLY the bill text provided. If it does not say something, you do not know it.
- Write plain English. No legalese, no hedging, no filler.
- Write SHORT. One idea per sentence, one point per paragraph. A reader is scanning this between meetings, not studying it. Long dense paragraphs are a failure even when every word is accurate.
- Every quote must be copied EXACTLY from the bill text, with no [[+ +]] or [[- -]] markers, and must be a span you can point to.
- Keep quotes SHORT -- 6 to 25 words of contiguous text. Never join two passages into one quote and never drop words from the middle. A short exact quote is worth more than a long approximate one; a quote that is not word-for-word will be discarded and your claim lost with it.
- [[+text+]] is language being ADDED to Florida law. [[-text-]] is language being DELETED from it. Unmarked text is existing law, shown for context, and is NOT changing.
- Read direction off the markers, never off the words inside them. A prohibition inside [[+ +]] is a NEW restriction the bill creates; those same words inside [[- -]] would be an existing restriction it repeals. Calling added language a removal states the exact opposite of what the bill does.
- Deletions are easy to miss and often matter, so say plainly when a requirement, protection or limit is being repealed -- but only when those words sit inside [[- -]]. A bill that deletes nothing of substance repeals nothing. Do not reach for a repeal that is not there.
- Many marked edits are punctuation, renumbering or cross-reference updates. A deleted comma is not a repeal, and a semicolon replacing a comma changes nothing at all -- least of all the clause that happens to follow it. Judge an edit by the words it changes.
- Never speculate about anyone's motives, party, or intent. Describe what the text does and what it permits.

How Florida drafts. These words are terms of art and do not carry their ordinary-English force:
- "MAY NOT" and "SHALL NOT" are BOTH mandatory prohibitions, equal in effect. "May not" is the form Florida prefers and is far more common in the statutes. It NEVER means "is permitted not to", and a prohibition is not weaker for using it. If the text says an assessment "may not be levied", then levying it is forbidden.
- "SHALL" and "MUST" both impose a mandatory duty.
- "MAY" on its own is permissive and grants discretion.
- "IS ENTITLED TO" confers a right.
Never argue that a prohibition is weak, optional or merely advisory because of which of these words it uses.

Before saying what a deletion does, decide what kind of clause was struck -- the kind decides the direction, and getting it wrong states the exact opposite of what the bill does:
- A GRANT OF AUTHORITY struck ("may apply for ...") means that route is gone.
- A RESTRICTIVE QUALIFIER struck ("of a 501(c)(3) organization") means scope WIDENS to reach more than before -- not less.
- A PRECONDITION struck ("after assurances have been provided") means the condition is gone, not that the underlying duty vanished.
- A CEILING struck ("may not exceed $1,500") means the limit is lifted; whatever duty sits under it is unchanged. A ceiling written as "may not exceed" or "shall not exceed" is not a prohibition on the underlying act: "compensation may not exceed 125 percent of the Medicare rate" permits everything under that cap, and striking the clause raises or removes the cap -- it does not forbid compensation.

The final line of the message names which task to perform.

TASK summary
Say what actually changes for people in practice, not what the bill is "about". Lead with whichever change reaches the most people or hits them hardest, whether that change creates a duty or removes one.

The one-line field must be a SHORT verb phrase naming the single most important effect -- 6 to 12 words, starting with a verb, ending in a full stop. The reader already knows it is a bill, so never write "This bill ...", and never preview what the paragraphs below will say. Cut every qualifier that is not load-bearing. Name the effect, not the machinery: say what becomes possible or forbidden, never "replaces X with Y" or "changes the process for Z". A hard limit cuts this field off mid-word, so if it will not fit in twelve words you have written the wrong sentence -- write a shorter one.

Then give two to four SHORT paragraphs of 25-45 words, one point each. Do not write a single long block.

TASK provisions
List the provisions that matter, most important first. Include a provision only if it changes what someone may, must, or cannot do. Skip renumbering, cross-reference updates and effective-date clauses unless they change substance. Prefer four well-chosen provisions to twelve trivial ones. Quote the exact language each one rests on.

TASK implications
Identify what this language would permit or require in practice. You are describing the reach of the text, not anyone's plans. For each entry, name a concrete situation the wording allows, and quote the words that allow it. Pay attention to: discretion granted without a standard to meet, terms left undefined, removed notice or review requirements, and penalties or duties whose scope is broader than the bill's stated subject.

If the text is narrow and its effects are plain, return few entries or none. Do not manufacture concerns. Never suggest what any person, party or sponsor wants -- only what the words themselves would allow.
```

### The three passes

| pass | max tokens | required fields |
|---|---:|---|
| `summary` | 900 | `one_line, summary, who_is_affected` |
| `provisions` | 2000 | `provisions` |
| `implications` | 2000 | `reading, implications, unclear` |

### Schemas

Enforced by the decoder as a grammar, so the model cannot emit JSON that
breaks them. Only shape is enforced -- a `description` never reaches the
model at all.

`summary`

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "one_line": {
      "type": "string",
      "minLength": 20,
      "maxLength": 110,
      "description": "The single most important effect, as a short verb phrase. START WITH A VERB. Never begin with 'This bill', 'The bill' or 'Provides for'. No preamble, no qualifiers, no detail the summary repeats below. 6-12 words, and it must be a complete phrase that ends before the limit -- do not name a process and then describe what replaces it. Good: 'Allows development without amending the local comprehensive plan.'"
    },
    "summary": {
      "type": "array",
      "minItems": 2,
      "maxItems": 4,
      "items": {
        "type": "string",
        "minLength": 60,
        "maxLength": 400
      },
      "description": "Short paragraphs, ONE point each, 25-45 words. Start with what the bill does, then what it removes or restricts, then who decides what afterwards. Never combine points into one paragraph."
    },
    "who_is_affected": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 3,
        "maxLength": 240
      },
      "description": "Groups of people or bodies directly affected."
    }
  },
  "required": [
    "one_line",
    "summary",
    "who_is_affected"
  ]
}
```

`provisions`

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "provisions": {
      "type": "array",
      "maxItems": 8,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "heading": {
            "type": "string",
            "minLength": 8,
            "maxLength": 120,
            "description": "Under 10 words."
          },
          "effect": {
            "type": "string",
            "minLength": 60,
            "maxLength": 500,
            "description": "What this provision does, in plain English."
          },
          "quote": {
            "type": "string",
            "minLength": 25,
            "maxLength": 180,
            "description": "A SHORT contiguous span of operative language copied EXACTLY from the bill text, without the [[+ +]] or [[- -]] markers. 6 to 25 words. Do not join text from two places, do not trim words from the middle, and do not quote the bill title. If you cannot copy it exactly, choose a shorter span you can."
          },
          "statute": {
            "type": "string",
            "maxLength": 80,
            "description": "Statute section, e.g. 493.6102. Empty string if none applies."
          },
          "significance": {
            "type": "string",
            "enum": [
              "major",
              "moderate",
              "technical"
            ]
          }
        },
        "required": [
          "heading",
          "effect",
          "quote",
          "statute",
          "significance"
        ]
      }
    }
  },
  "required": [
    "provisions"
  ]
}
```

`implications`

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "reading": {
      "type": "string",
      "minLength": 150,
      "maxLength": 2200,
      "description": "Before listing anything: in 2-3 sentences, what does this bill change about who may, must, or cannot do what? Say plainly what it removes from existing law."
    },
    "implications": {
      "type": "array",
      "maxItems": 6,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "consequence": {
            "type": "string",
            "minLength": 60,
            "maxLength": 700,
            "description": "A concrete situation this language would permit or require. Describe the text's effect. Never state or imply what any person or party wants or intends."
          },
          "quote": {
            "type": "string",
            "minLength": 25,
            "maxLength": 180,
            "description": "A SHORT contiguous span of operative language copied EXACTLY from the bill text, without the [[+ +]] or [[- -]] markers. 6 to 25 words. Do not join text from two places, do not trim words from the middle, and do not quote the bill title. If you cannot copy it exactly, choose a shorter span you can."
          },
          "certainty": {
            "type": "string",
            "enum": [
              "follows_directly",
              "plausible",
              "speculative"
            ],
            "description": "follows_directly: the text plainly requires it. plausible: the text permits it. speculative: depends on how it is applied."
          }
        },
        "required": [
          "consequence",
          "quote",
          "certainty"
        ]
      }
    },
    "unclear": {
      "type": "array",
      "maxItems": 5,
      "items": {
        "type": "string",
        "minLength": 20,
        "maxLength": 1000
      },
      "description": "Terms left undefined, or discretion left unbounded."
    }
  },
  "required": [
    "reading",
    "implications",
    "unclear"
  ]
}
```
<!-- END GENERATED PROMPT -->
