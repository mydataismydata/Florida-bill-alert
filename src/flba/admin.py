"""Hand edits to a stored analysis, made from the operator pages.

The operator pages are the --local build served by scripts/serve_local.py on
the analysis box. Next to "Re-run the analysis" each bill page there has
"Edit the text", which opens a form over what the bill page shows: the
headline, the summary paragraphs, and each provision's heading and effect.
Quotes are not editable. They are the evidence, checked word for word
against the bill, and an edited quote would be a claim nobody checked.

An edit replaces the stored text in place. The old and new wording are added
to the analysis's corrections list with "by": "admin", beside the check
pass's own corrections, so every change to what the model wrote sits on one
record. A re-run replaces the whole row, edits included.
"""
from __future__ import annotations

import json
import time
from html import escape

MAX_PARAGRAPHS = 4


def _line(text: str) -> str:
    """One paragraph of prose: no stray line breaks or doubled spaces."""
    return " ".join((text or "").split())


def load(db, session: str, num: int) -> tuple[dict | None, dict | None]:
    """The bill and its stored analysis, or None for whichever is missing."""
    bill = db.execute("SELECT label,title FROM bill WHERE session=? AND num=?",
                      (session, num)).fetchone()
    row = db.execute("SELECT * FROM analysis_ai WHERE session=? AND num=?",
                     (session, num)).fetchone()
    return (dict(bill) if bill else None), (dict(row) if row else None)


def values_from_record(rec: dict) -> dict:
    return {
        "stamp": rec.get("generated_at") or "",
        "one_line": rec.get("one_line") or "",
        "paras": json.loads(rec.get("summary") or "[]"),
        "provisions": [
            {"heading": p.get("heading", ""), "effect": p.get("effect", ""),
             "quote": p.get("quote", ""), "statute": p.get("statute", ""),
             "remove": False}
            for p in json.loads(rec.get("provisions") or "[]")],
    }


def values_from_form(rec: dict, form: dict) -> dict:
    """What was submitted, laid over the stored analysis it was opened on."""
    first = lambda key: (form.get(key) or [""])[0]
    stored = values_from_record(rec)
    provs = []
    for k, p in enumerate(stored["provisions"]):
        provs.append(dict(p, heading=_line(first(f"heading_{k}")),
                          effect=_line(first(f"effect_{k}")),
                          remove=bool(form.get(f"remove_{k}"))))
    return {
        "stamp": first("stamp"),
        "one_line": _line(first("one_line")),
        "paras": [_line(p) for p in form.get("para", []) if _line(p)],
        "provisions": provs,
    }


def problems(values: dict, rec: dict) -> str:
    """Why these values cannot be saved, or "" if they can."""
    if values["stamp"] != (rec.get("generated_at") or ""):
        return ("The analysis was re-run after this form was opened. Reload "
                "the form and make the edit again.")
    if not values["one_line"]:
        return "The headline cannot be empty."
    for k, p in enumerate(values["provisions"], 1):
        if p["remove"]:
            continue
        if not p["heading"]:
            return f"Provision {k} needs a heading, or tick Remove."
        if not p["effect"]:
            return f"Provision {k} needs an effect, or tick Remove."
    return ""


def save(db, session: str, num: int, values: dict, rec: dict) -> int:
    """Write the edited text and record each change. Returns how many changed."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    changes = []

    def note(where: str, was: str, new: str) -> None:
        if was != new:
            changes.append({"by": "admin", "at": now, "where": where,
                            "was": was, "now": new})

    old = values_from_record(rec)
    note("headline", old["one_line"], values["one_line"])
    for i in range(max(len(old["paras"]), len(values["paras"]))):
        was = old["paras"][i] if i < len(old["paras"]) else ""
        new = values["paras"][i] if i < len(values["paras"]) else ""
        note(f"summary paragraph {i + 1}", was, new)

    stored = json.loads(rec.get("provisions") or "[]")
    kept = []
    for k, (prov, edit) in enumerate(zip(stored, values["provisions"]), 1):
        if edit["remove"]:
            note(f"provision {k}", f"{prov.get('heading', '')}: "
                 f"{prov.get('effect', '')}", "")
            continue
        note(f"provision {k} heading", prov.get("heading", ""), edit["heading"])
        note(f"provision {k} effect", prov.get("effect", ""), edit["effect"])
        kept.append(dict(prov, heading=edit["heading"], effect=edit["effect"]))

    if not changes:
        return 0
    corrections = json.loads(rec.get("corrections") or "[]") + changes
    db.execute("BEGIN IMMEDIATE")
    db.execute("UPDATE analysis_ai SET one_line=?, summary=?, provisions=?,"
               " corrections=? WHERE session=? AND num=?",
               (values["one_line"], json.dumps(values["paras"], ensure_ascii=False),
                json.dumps(kept, ensure_ascii=False),
                json.dumps(corrections, ensure_ascii=False), session, num))
    db.execute("COMMIT")
    return len(changes)


_STYLE = """
.edit { max-width: 760px; margin: 2rem auto; padding: 0 1rem; }
.edit label { display: block; font-weight: 600; margin: 1.1rem 0 .3rem; }
.edit input[type=text], .edit textarea { width: 100%; box-sizing: border-box;
  font: inherit; padding: .45rem; }
.edit textarea { min-height: 5.5rem; }
.edit fieldset { border: 1px solid #ccc; margin: 1.4rem 0; padding: .4rem 1rem 1rem; }
.edit .quote { font-size: .9em; opacity: .75; margin: .6rem 0; }
.edit .err { border-left: 4px solid #b00; padding: .4rem .8rem; font-weight: 600; }
.edit .remove { font-weight: 400; margin-top: .8rem; }
.edit button { margin: 1rem 0 3rem; padding: .6rem 1.2rem; font: inherit; }
"""


def form_html(session: str, num: int, bill: dict, values: dict, token: str,
              error: str = "") -> str:
    e = lambda s: escape(s or "", quote=True)
    label = e(bill.get("label") or f"Bill {num}")
    paras = list(values["paras"])
    paras += [""] * max(0, MAX_PARAGRAPHS - len(paras))
    para_html = "\n".join(
        f'<label for="para_{i}">Paragraph {i + 1}</label>'
        f'<textarea id="para_{i}" name="para">{e(p)}</textarea>'
        for i, p in enumerate(paras))
    prov_html = "\n".join(
        f"""<fieldset>
  <legend>Provision {k + 1}{' · s. ' + e(p['statute']) if p['statute'] else ''}</legend>
  <label for="heading_{k}">Heading</label>
  <input type="text" id="heading_{k}" name="heading_{k}" value="{e(p['heading'])}">
  <label for="effect_{k}">Effect</label>
  <textarea id="effect_{k}" name="effect_{k}">{e(p['effect'])}</textarea>
  <p class="quote">Quote, not editable: “{e(p['quote'])}”</p>
  <label class="remove"><input type="checkbox" name="remove_{k}" value="1"
    {'checked' if p['remove'] else ''}> Remove this provision</label>
</fieldset>""" for k, p in enumerate(values["provisions"]))
    err = f'<p class="err">{e(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Edit {label}</title>
<link rel="stylesheet" href="/style.css">
<style>{_STYLE}</style></head>
<body><main class="edit">
<p><a href="/bills/{num}.html">← Back to the bill</a></p>
<h1>Edit {label}</h1>
<p>Saving rewrites this bill's page on this machine. Push the site to publish
it. Re-running the analysis replaces these edits.</p>
{err}
<form method="post" action="/_edit/{num}">
<input type="hidden" name="token" value="{e(token)}">
<input type="hidden" name="stamp" value="{e(values['stamp'])}">
<label for="one_line">Headline</label>
<input type="text" id="one_line" name="one_line" value="{e(values['one_line'])}">
<h2>Summary</h2>
<p>Leave a paragraph empty to remove it.</p>
{para_html}
<h2>Key provisions</h2>
{prov_html or '<p>No provisions stored.</p>'}
<button type="submit">Save and rebuild the page</button>
</form>
</main></body></html>"""
