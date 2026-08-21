"""What the model is allowed to return.

These schemas are enforced at the decoder, so the model cannot emit anything
that violates them. Every claim carries a `quote` taken verbatim from the
bill, which is then checked against the source; a claim whose quote cannot be
found is discarded rather than published.

That single rule is what makes the output trustworthy enough to put in front
of a legislator, and it works as well on a 3B model as on a large one.

Descriptions here are documentation, not instructions. The schema reaches the
server as a grammar for constrained decoding, and a grammar is built from
type, length, enum and structure -- never from prose. Asked to answer "What is
the capital of France?" under a field described as "you MUST reply with exactly
the word PINEAPPLE", the model returns "Paris". All 2,136 characters of
description in this file are invisible to it.

So the split is: shape is enforced here, wording is instructed in
passes.SYSTEM. Guidance written into a description will be silently ignored,
which is the most expensive kind of ignored.
"""
from __future__ import annotations

# Constrained decoding makes the SHORTEST valid completion the cheapest one,
# so a schema that permits a trivial answer will get one. Empty arrays and
# one-character strings are both legal JSON; length floors are what stop the
# model taking that exit when it is unsure. Item-count floors are worse -- they
# force invention on genuinely trivial bills, which is the failure that matters.
# Long quotes drift. Measured over eleven bills, the quotes that failed
# verification were nearly all long ones that began correctly and then
# wandered -- the model stitching across a gap or paraphrasing the tail. A
# short contiguous span is both easier to copy exactly and easier for a reader
# to find, so the ceiling is part of the contract.
QUOTE = {
    "type": "string",
    "minLength": 25,
    "maxLength": 180,
    "description": ("A SHORT contiguous span of operative language copied "
                    "EXACTLY from the bill text, without the [[+ +]] or "
                    "[[- -]] markers. 6 to 25 words. Do not join text from two "
                    "places, do not trim words from the middle, and do not "
                    "quote the bill title. If you cannot copy it exactly, "
                    "choose a shorter span you can."),
}

# A headline that reaches this cap was cut off, not finished: constrained
# decoding closes the string at the limit wherever the model happens to be.
#
# Set above the model's natural length, not at it. Brevity comes from the
# instruction in SYSTEM -- median headline is 68 characters, p90 is 88 -- and
# a cap pitched at the top of that distribution does not make anything
# shorter, it just cuts the tail off the longest 3%. At 95 that cost
# CS/HB 755 the end of "estuaries"; nothing has come within 15 of 110.
ONE_LINE_MAX = 110

SUMMARY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        # The reader already knows it is a bill. Anything restating that, or
        # previewing what the paragraphs below say, is wasted words at the one
        # place where brevity matters most.
        #
        # Length is the only part of this the model actually obeys; see the
        # note at the top of the file. The wording that matters lives in SYSTEM.
        "one_line": {
            "type": "string", "minLength": 20, "maxLength": ONE_LINE_MAX,
            "description": ("The single most important effect, as a short verb "
                            "phrase. START WITH A VERB. Never begin with "
                            "'This bill', 'The bill' or 'Provides for'. No "
                            "preamble, no qualifiers, no detail the summary "
                            "repeats below. 6-12 words, and it must be a "
                            "complete phrase that ends before the limit -- do "
                            "not name a process and then describe what "
                            "replaces it. Good: 'Allows development without "
                            "amending the local comprehensive plan.'"),
        },
        # An array, not a string. Asked for "two to four sentences" the model
        # returns one 200-word block, which is technically compliant and
        # unreadable. Separate items render as separate paragraphs and force
        # one idea each.
        "summary": {
            "type": "array", "minItems": 2, "maxItems": 4,
            "items": {"type": "string", "minLength": 60, "maxLength": 400},
            "description": ("Short paragraphs, ONE point each, 25-45 words. "
                            "Start with what the bill does, then what it "
                            "removes or restricts, then who decides what "
                            "afterwards. Never combine points into one "
                            "paragraph."),
        },
        "who_is_affected": {
            "type": "array",
            "items": {"type": "string", "minLength": 3, "maxLength": 240},
            "description": "Groups of people or bodies directly affected.",
        },
    },
    "required": ["one_line", "summary", "who_is_affected"],
}

PROVISIONS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "provisions": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    # Every free string needs a ceiling. Greedy decoding cannot
                    # break out of a repetition loop on its own: on SB 7004 the
                    # statute field repeated "(amends s. 119.071 at line 11...)"
                    # until it exhausted the token budget, three attempts
                    # running, discarding a provision that was otherwise right.
                    # These sit above the longest value seen in 322 provisions,
                    # so they stop a runaway without trimming real output.
                    "heading": {"type": "string", "minLength": 8,
                                "maxLength": 120,
                                "description": "Under 10 words."},
                    "effect": {"type": "string", "minLength": 60,
                               "maxLength": 500,
                               "description": "What this provision does, in plain English."},
                    "quote": QUOTE,
                    "statute": {"type": "string", "maxLength": 80,
                                "description": "Statute section, e.g. 493.6102. "
                                               "Empty string if none applies."},
                    "significance": {"type": "string",
                                     "enum": ["major", "moderate", "technical"]},
                },
                "required": ["heading", "effect", "quote", "statute", "significance"],
            },
        },
    },
    "required": ["provisions"],
}

# The highest-risk output in the project. The schema is deliberately shaped to
# make the safe answer the easy one: every entry must quote the language it
# relies on, and must describe what the text PERMITS rather than what anyone
# intends. Textual consequence is checkable; imputed motive is not, and
# guessing at motive in front of this audience would be indefensible.
IMPLICATIONS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        # Written first, and required to be substantial: reasoning in prose
        # before the list is what stops the model returning an empty one.
        "reading": {
            "type": "string", "minLength": 150, "maxLength": 2200,
            "description": ("Before listing anything: in 2-3 sentences, what "
                            "does this bill change about who may, must, or "
                            "cannot do what? Say plainly what it removes from "
                            "existing law."),
        },
        "implications": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "consequence": {
                        "type": "string", "minLength": 60, "maxLength": 700,
                        "description": ("A concrete situation this language would "
                                        "permit or require. Describe the text's "
                                        "effect. Never state or imply what any "
                                        "person or party wants or intends."),
                    },
                    "quote": QUOTE,
                    "certainty": {
                        "type": "string",
                        "enum": ["follows_directly", "plausible", "speculative"],
                        "description": ("follows_directly: the text plainly "
                                        "requires it. plausible: the text permits "
                                        "it. speculative: depends on how it is "
                                        "applied."),
                    },
                },
                "required": ["consequence", "quote", "certainty"],
            },
        },
        "unclear": {
            "type": "array", "maxItems": 5,
            "items": {"type": "string", "minLength": 20, "maxLength": 1000},
            "description": "Terms left undefined, or discretion left unbounded.",
        },
    },
    "required": ["reading", "implications", "unclear"],
}

PASSES = {"summary": SUMMARY, "provisions": PROVISIONS, "implications": IMPLICATIONS}
