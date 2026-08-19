"""What the model is allowed to return.

These schemas are enforced at the decoder, so the model cannot emit anything
that violates them. Every claim carries a `quote` taken verbatim from the
bill, which is then checked against the source; a claim whose quote cannot be
found is discarded rather than published.

That single rule is what makes the output trustworthy enough to put in front
of a legislator, and it works as well on a 3B model as on a large one.
"""
from __future__ import annotations

QUOTE = {
    "type": "string",
    "description": ("A short span copied EXACTLY from the bill text, without "
                    "the [[+ +]] or [[- -]] markers. 4 to 40 words."),
}

SUMMARY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "one_line": {"type": "string",
                     "description": "One sentence, under 25 words, plain English."},
        "summary": {"type": "string",
                    "description": "Two to four sentences in plain English. "
                                   "No legal jargon. Say what changes in practice."},
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
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "heading": {"type": "string",
                                "description": "Under 10 words."},
                    "effect": {"type": "string",
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
        "implications": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "consequence": {
                        "type": "string",
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
            "type": "array", "items": {"type": "string"},
            "description": "Terms left undefined, or discretion left unbounded.",
        },
    },
    "required": ["implications", "unclear"],
}

PASSES = {"summary": SUMMARY, "provisions": PROVISIONS, "implications": IMPLICATIONS}
