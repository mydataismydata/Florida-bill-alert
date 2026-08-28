#!/usr/bin/env python3
"""Write the live prompt into docs/ANALYSIS-NOTES.md.

The prompt used to be published as a page per bill, built from this same
module, so it could not drift from what was sent. The pages are gone -- nothing
linked to them, and they showed the current prompt beside answers produced by
an older one, which is a claim the page could not keep. The prompt is
documented here instead, and tests/test_site.py fails when this file and
passes.py disagree, so "documented" keeps meaning "the same words the model
gets".

    PYTHONPATH=src ./.venv/bin/python scripts/sync_prompt_doc.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from flba.analysis import passes as P          # noqa: E402

DOC = ROOT / "docs" / "ANALYSIS-NOTES.md"
START = "<!-- BEGIN GENERATED PROMPT -->"
END = "<!-- END GENERATED PROMPT -->"


def section() -> str:
    rows = "\n".join(
        f"| `{n}` | {P.BUDGET[n]} | `{', '.join(P.SCHEMAS[n].get('required', []))}` |"
        for n in P.ORDER)
    schemas = "\n\n".join(
        f"`{n}`\n\n```json\n{json.dumps(P.SCHEMAS[n], indent=2)}\n```"
        for n in P.ORDER)
    return f"""{START}
## The prompt, verbatim

Generated from `src/flba/analysis/passes.py` by `scripts/sync_prompt_doc.py`.
A test fails if the two fall out of step, so this is the text the model is
actually given -- not a description of it.

The system message is identical for all three passes; only a short task
selector at the end of the user turn differs, which is what keeps the cached
prefix usable.

```text
{P.SYSTEM.rstrip()}
```

### The three passes

| pass | max tokens | required fields |
|---|---:|---|
{rows}

### Schemas

Enforced by the decoder as a grammar, so the model cannot emit JSON that
breaks them. Only shape is enforced -- a `description` never reaches the
model at all.

{schemas}
{END}"""


def main() -> int:
    doc = DOC.read_text()
    new = section()
    if START in doc and END in doc:
        head, rest = doc.split(START, 1)
        _old, tail = rest.split(END, 1)
        out = head + new + tail
    else:
        out = doc.rstrip() + "\n\n" + new + "\n"
    if out == doc:
        print("docs/ANALYSIS-NOTES.md already current")
        return 0
    DOC.write_text(out)
    print(f"docs/ANALYSIS-NOTES.md updated ({len(new):,} chars generated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
