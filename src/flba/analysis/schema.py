"""What the model is allowed to return.

These schemas are enforced at the decoder, so the model cannot emit anything
that violates them. Every claim carries a `quote` taken verbatim from the
bill, which is then checked against the source; a claim whose quote cannot be
found is discarded rather than published.

That single rule is what makes the output trustworthy enough to put in front
of a legislator, and it works as well on a 3B model as on a large one.
"""
from __future__ import annotations

# Constrained decoding makes the SHORTEST valid completion the cheapest one,
# so a schema that permits a trivial answer will get one. Empty arrays and
# one-character strings are both legal JSON; length floors are what stop the
# model taking that exit when it is unsure. Item-count floors are worse -- they
# force invention on genuinely trivial bills, which is the failure that matters.
QUOTE = {
    "type": "string",
    "minLength": 25,
    "description": ("A span of OPERATIVE language copied EXACTLY from the bill "
                    "text, without the [[+ +]] or [[- -]] markers. Not the bill "
                    "title. 4 to 40 words."),
}

SUMMARY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "one_line": {"type": "string", "minLength": 40,
                     "description": "One sentence, under 25 words, plain English."},
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
            "type": "array", "items": {"type": "string"},
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
                    "heading": {"type": "string", "minLength": 8,
                                "description": "Under 10 words."},
                    "effect": {"type": "string", "minLength": 60,
                               "description": "What this provision does, in plain English."},
                    "quote": QUOTE,
                    "statute": {"type": "string",
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
            "type": "string", "minLength": 150,
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
                        "type": "string", "minLength": 60,
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
            "type": "array", "maxItems": 5, "items": {"type": "string"},
            "description": "Terms left undefined, or discretion left unbounded.",
        },
    },
    "required": ["reading", "implications", "unclear"],
}

PASSES = {"summary": SUMMARY, "provisions": PROVISIONS, "implications": IMPLICATIONS}
