"""Render the whole tracker as static files.

The public server holds no model, no pipeline, and no database -- only files.
That is what keeps the gap between the analysis machine and the public site
real rather than merely configured, and it is what lets the site live on
ordinary shared hosting.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .stages import kind_of, pathway, track

HERE = Path(__file__).resolve().parent
SITE_NAME = "Florida Bill Alert"
REPO = "https://github.com/mydataismydata/Florida-bill-alert"
MAX_CHANGES = 60          # per bill page; the rest stay one click away
SAFE_CITE = re.compile(r"^[0-9A-Za-z.]+$")


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(HERE / "templates")),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True, lstrip_blocks=True)
    return env


def _rows(db, sql, *args):
    return db.execute(sql, args).fetchall()


def build(db_path: Path, out: Path, session: str, built: str | None = None,
          limit: int = 0, log=print) -> dict:
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    env = _env()
    out = Path(out)
    (out / "bills").mkdir(parents=True, exist_ok=True)
    (out / "statutes").mkdir(parents=True, exist_ok=True)
    built = built or date.today().isoformat()

    bills = _rows(db, "SELECT * FROM bill WHERE session=? ORDER BY num", session)
    if limit:
        bills = bills[:limit]
    # A partial build must not emit links to pages it never wrote, so every
    # cross-reference below is scoped to the bills actually rendered.
    built_nums = {b["num"] for b in bills}
    if not bills:
        raise SystemExit(f"no bills ingested for session {session}")

    history: dict[int, list] = {}
    for r in _rows(db, "SELECT num,date,chamber,action FROM history"
                       " WHERE session=? ORDER BY num,seq", session):
        history.setdefault(r["num"], []).append(dict(r))

    common = dict(site_name=SITE_NAME, session=session, built=built,
                  repo=REPO, bill_count=len(bills))

    # ---------------------------------------------------------- bill pages
    index_rows, outcomes, enacted = [], {}, []
    progress: dict[int, object] = {}
    for i, b in enumerate(bills, 1):
        prog = track(history.get(b["num"], []), b["chapter_law"],
                     kind_of(b["label"]))
        progress[b["num"]] = prog
        outcomes[prog.outcome] = outcomes.get(prog.outcome, 0) + 1

        refs = _rows(db, "SELECT * FROM statute_ref WHERE session=? AND num=?"
                         " ORDER BY bill_section IS NULL, bill_section, statute",
                     session, b["num"])
        changes = _rows(db, "SELECT kind,line,page,text FROM change"
                            " WHERE session=? AND num=? ORDER BY seq",
                        session, b["num"])
        analyses = _rows(db, "SELECT kind,author,posted,url FROM analysis"
                             " WHERE session=? AND num=? ORDER BY posted",
                         session, b["num"])
        shown = [dict(c) for c in changes[:MAX_CHANGES]]
        stats = {
            "words_inserted": sum(len(c["text"].split())
                                  for c in changes if c["kind"] == "insert"),
            "words_deleted": sum(len(c["text"].split())
                                 for c in changes if c["kind"] == "delete"),
        }

        html = env.get_template("bill.html").render(
            root="../", b=b, p=prog, path=pathway(prog), refs=refs,
            changes=shown, total_changes=len(changes),
            truncated=len(changes) > MAX_CHANGES, stats=stats,
            analyses=analyses, history=history.get(b["num"], []), **common)
        (out / "bills" / f"{b['num']}.html").write_text(html, encoding="utf-8")

        index_rows.append({
            "n": b["num"], "l": b["label"], "t": (b["title"] or "")[:120],
            "o": prog.outcome_label,
            "s": " ".join(filter(None, [
                b["label"], b["title"], b["sponsor"], b["cosponsors"]])).lower(),
        })
        if b["chapter_law"]:
            enacted.append(b)
        if i % 400 == 0:
            log(f"  {i}/{len(bills)} bill pages")

    (out / "search-index.json").write_text(
        json.dumps(index_rows, separators=(",", ":")), encoding="utf-8")

    # ------------------------------------------------------ statute pages
    stat_rows = []
    for r in _rows(db,
            "SELECT statute, num, words_added, words_deleted"
            "  FROM statute_ref WHERE session=?", session):
        if r["num"] not in built_nums:
            continue
        stat_rows.append(r)
    agg: dict[str, dict] = {}
    for r in stat_rows:
        a = agg.setdefault(r["statute"], {"statute": r["statute"],
                                          "nums": set(), "added": 0, "deleted": 0})
        a["nums"].add(r["num"])
        a["added"] += r["words_added"] or 0
        a["deleted"] += r["words_deleted"] or 0
    stat_rows = sorted(
        ({**a, "bills": len(a["nums"])} for a in agg.values()),
        key=lambda a: (-a["bills"], a["statute"]))

    written_statutes = 0
    for s in stat_rows:
        cite = s["statute"]
        if not SAFE_CITE.match(cite):        # never let a citation escape the dir
            continue
        rows = []
        for r in _rows(db,
                "SELECT DISTINCT r.num, b.label, b.title, r.action,"
                "       r.words_added, r.words_deleted"
                "  FROM statute_ref r JOIN bill b"
                "    ON b.session=r.session AND b.num=r.num"
                " WHERE r.session=? AND r.statute=? ORDER BY r.num",
                session, cite):
            if r["num"] not in built_nums:
                continue
            p = progress.get(r["num"])
            rows.append(dict(r, outcome=p.outcome if p else "pending",
                             outcome_label=p.outcome_label if p else "—"))
        html = env.get_template("statute.html").render(
            root="../", cite=cite, rows=rows, **common)
        (out / "statutes" / f"{cite}.html").write_text(html, encoding="utf-8")
        written_statutes += 1

    (out / "statutes" / "index.html").write_text(
        env.get_template("statutes.html").render(root="../", rows=stat_rows,
                                                 **common), encoding="utf-8")

    # ------------------------------------------------------- front and about
    order = ["became_law", "died", "superseded", "adopted", "vetoed",
             "pending", "to_ballot"]
    from .stages import OUTCOME_LABEL
    outcome_cards = [{"label": OUTCOME_LABEL[k], "n": outcomes[k]}
                     for k in order if outcomes.get(k)]

    (out / "index.html").write_text(env.get_template("index.html").render(
        root="", outcomes=outcome_cards,
        enacted=sorted(enacted, key=lambda b: b["num"]),
        top_statutes=stat_rows[:10], **common), encoding="utf-8")

    (out / "about.html").write_text(env.get_template("about.html").render(
        root="", superseded=outcomes.get("superseded", 0), **common),
        encoding="utf-8")

    shutil.copy(HERE / "static" / "style.css", out / "style.css")

    files = sum(1 for _ in out.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    return {"bills": len(bills), "statutes": written_statutes,
            "files": files, "bytes": size, "outcomes": outcomes}
