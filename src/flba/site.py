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

from .analysis import passes as P
from .areas import AREAS, classify, slug
from .analysis.analyze import RETRIES
from .analysis.analyze import tidy_statute
from .analysis.brief import build as build_brief
from .diff import PLAIN, BillDiff, Segment, context_blocks, locate
from .diff import lines as doc_lines
from .stages import kind_of, pathway, track

HERE = Path(__file__).resolve().parent
SITE_NAME = "Session Watch"
REPO = "https://github.com/mydataismydata/Florida-bill-alert"
MAX_BLOCKS = 12           # changed passages on the summary page
SHOWN_PROVISIONS = 2      # the rest are behind "N more provisions"

# Florida's 67, for the subscribe form's optional county field.
COUNTIES = ['Alachua', 'Baker', 'Bay', 'Bradford', 'Brevard', 'Broward', 'Calhoun', 'Charlotte', 'Citrus', 'Clay', 'Collier', 'Columbia', 'DeSoto', 'Dixie', 'Duval', 'Escambia', 'Flagler', 'Franklin', 'Gadsden', 'Gilchrist', 'Glades', 'Gulf', 'Hamilton', 'Hardee', 'Hendry', 'Hernando', 'Highlands', 'Hillsborough', 'Holmes', 'Indian River', 'Jackson', 'Jefferson', 'Lafayette', 'Lake', 'Lee', 'Leon', 'Levy', 'Liberty', 'Madison', 'Manatee', 'Marion', 'Martin', 'Miami-Dade', 'Monroe', 'Nassau', 'Okaloosa', 'Okeechobee', 'Orange', 'Osceola', 'Palm Beach', 'Pasco', 'Pinellas', 'Polk', 'Putnam', 'St. Johns', 'St. Lucie', 'Santa Rosa', 'Sarasota', 'Seminole', 'Sumter', 'Suwannee', 'Taylor', 'Union', 'Volusia', 'Wakulla', 'Walton', 'Washington']

KIND_NAMES = {0: "plain", 1: "insert", 2: "delete"}
SAFE_CITE = re.compile(r"^[0-9A-Za-z.]+$")


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(HERE / "templates")),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True, lstrip_blocks=True)
    return env


def _rows(db, sql, *args):
    return db.execute(sql, args).fetchall()


# House committees carry the word in their name; Senate ones do not -- "Rules"
# and "Judiciary" are committees, and so are "Martin" and "McClain" as far as
# any pattern can tell. So the Senate names are matched against the vocabulary
# the deterministic layer already collected rather than guessed at.
_COMMITTEE_WORD = re.compile(r"\b(?:Committee|Subcommittee)\b")


# "1002.33(10)(e)" is a real citation and worth showing, but the page is
# 1002.33 -- keeping the subsection in the href produced a 404 on every
# provision the model cited that way.
_SUBSECTION = re.compile(r"\s*\(.*$")
# analyze.tidy_statute strips this at storage time; repeated here so the
# resolver is correct on its own and on anything stored before it did.
_CITE_PREFIX = re.compile(r"^(?:ss?\.|section)\s*", re.I)


def statute_page(cite: str, available: set) -> str:
    """The statute page a citation should link to, or '' if there is none."""
    base = _CITE_PREFIX.sub("", (cite or "").strip())
    base = _SUBSECTION.sub("", base).rstrip(".")
    return base if base in available else ""


def split_sponsor(sponsor: str, committees: set) -> tuple[list, list]:
    """Separate the committees that produced substitutes from the members who
    filed the bill.

    A bill that has been through three committees reads "Rules; Judiciary;
    Community Affairs; McClain", and only the last of those is a person anyone
    can write to. Across the 2026 session a committee never follows a member in
    this field, but the split does not rely on position -- it names them.
    """
    people, panels = [], []
    for part in (p.strip() for p in (sponsor or "").split(";")):
        if not part:
            continue
        (panels if part in committees or _COMMITTEE_WORD.search(part)
         else people).append(part)
    return panels, people


def _load_ai(db, session, num):
    row = db.execute("SELECT * FROM analysis_ai WHERE session=? AND num=?",
                     (session, num)).fetchone()
    if not row:
        return None
    out = dict(row)
    for key in ("summary", "who_is_affected", "provisions", "implications",
                "unclear", "dropped", "flagged", "failures", "stats"):
        try:
            out[key] = json.loads(out[key] or "[]")
        except Exception:
            out[key] = []
    if not isinstance(out.get("stats"), dict):
        out["stats"] = {}
    return out


def _load_render(db, session, num):
    row = db.execute("SELECT version,fmt,segments FROM bill_render"
                     " WHERE session=? AND num=?", (session, num)).fetchone()
    if not row:
        return None
    segs = [Segment(KIND_NAMES[k], t, ln, pg)
            for k, ln, pg, t in json.loads(row["segments"])]
    return row["version"], row["fmt"], BillDiff(row["fmt"], segs)


def build(db_path: Path, out: Path, session: str, built: str | None = None,
          limit: int = 0, local: bool = False, only: int | None = None,
          log=print) -> dict:
    """Render the static site.

    `local` adds operator controls that only work behind scripts/serve_local.py
    and must never reach the public host, which holds no model, no pipeline and
    no database. It defaults off so a plain build is always safe to deploy.
    """
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    env = _env()
    out = Path(out)
    (out / "bills").mkdir(parents=True, exist_ok=True)
    (out / "statutes").mkdir(parents=True, exist_ok=True)
    built = built or date.today().isoformat()

    # Written before anything can return early, because --only exits long
    # before the tail of this function. deploy.sh refuses to push a tree
    # carrying this file: the operator controls only work against a server on
    # this machine, and the public host must stay a file host.
    marker = out / ".local-build"
    if local:
        marker.write_text(
            "Built with --local: this tree carries operator controls that post\n"
            "to scripts/serve_local.py. Do not deploy it. Rebuild without\n"
            "--local to publish.\n", encoding="utf-8")
    elif marker.exists():
        marker.unlink()

    bills = _rows(db, "SELECT * FROM bill WHERE session=? ORDER BY num", session)
    if limit:
        bills = bills[:limit]
    # A partial build must not emit links to pages it never wrote, so every
    # cross-reference below is scoped to the bills actually rendered.
    built_nums = {b["num"] for b in bills}
    if only is not None:
        # Refresh one bill over a site that is already complete. The rest of
        # the tree stays on disk, so cross-references may still point at it --
        # what must not happen is scoping links to the single bill rebuilt.
        bills = [b for b in bills if b["num"] == only]
        if not bills:
            raise SystemExit(f"bill {only} is not in session {session}")
    if not bills:
        raise SystemExit(f"no bills ingested for session {session}")

    # Which statutes will actually get a page. The model cites subsections --
    # "1002.33(10)(e)" -- and pages are keyed on the bare number, so a claim's
    # link has to be resolved against this rather than trusted.
    statute_pages = {r["statute"] for r in
                     _rows(db, "SELECT DISTINCT statute, num FROM statute_ref"
                               " WHERE session=?", session)
                     if r["num"] in built_nums and SAFE_CITE.match(r["statute"])}

    committees = {r["name"] for r in
                  _rows(db, "SELECT DISTINCT name FROM committee_ref")}
    # Keyed by the member page URL, which is what a bill row already carries.
    members_by_url = {r["url"]: dict(r) for r in
                      _rows(db, "SELECT * FROM member")}

    history: dict[int, list] = {}
    for r in _rows(db, "SELECT num,date,chamber,action FROM history"
                       " WHERE session=? ORDER BY num,seq", session):
        history.setdefault(r["num"], []).append(dict(r))

    common = dict(site_name=SITE_NAME, session=session, built=built,
                  repo=REPO, bill_count=len(bills), local=local,
                  areas=[{"name": a, "slug": slug(a)} for a in AREAS],
                  page="")

    # ---------------------------------------------------------- bill pages
    index_rows, outcomes, enacted = [], {}, []
    by_area: dict = {}
    by_area_of: dict = {}
    progress: dict[int, object] = {}
    for i, b in enumerate(bills, 1):
        prog = track(history.get(b["num"], []), b["chapter_law"],
                     kind_of(b["label"]))
        progress[b["num"]] = prog
        outcomes[prog.outcome] = outcomes.get(prog.outcome, 0) + 1

        refs = _rows(db, "SELECT * FROM statute_ref WHERE session=? AND num=?"
                         " ORDER BY bill_section IS NULL, bill_section, statute",
                     session, b["num"])
        ai = _load_ai(db, session, b["num"])
        analyses = _rows(db, "SELECT kind,author,posted,url FROM analysis"
                             " WHERE session=? AND num=? ORDER BY posted",
                         session, b["num"])
        loaded = _load_render(db, session, b["num"])
        blocks, all_blocks, stats, version, fmt = [], [], {}, None, None
        if loaded:
            version, fmt, d = loaded
            nchars = sum(len(s.text) for s in d.segments)
            # Anchor each claim to the line its quote sits on, so a reader can
            # jump to the words themselves rather than to the statute at large.
            if ai:
                for claim in (ai.get("provisions") or []) + (ai.get("implications") or []):
                    claim["line"] = locate(d, claim.get("quote", ""))
                    # also applied at analysis time; repeated here so the
                    # analyses stored before it existed render clean too
                    claim["statute"] = tidy_statute(claim.get("statute", ""))
                    claim["statute_href"] = statute_page(
                        claim.get("statute", ""), statute_pages)
            all_blocks = context_blocks(d)
            blocks = all_blocks[:MAX_BLOCKS]
            stats = {
                "words_inserted": sum(len(s.text.split())
                                      for s in d.segments if s.kind == "insert"),
                "words_deleted": sum(len(s.text.split())
                                     for s in d.segments if s.kind == "delete"),
            }
            # the whole bill, marked, with the Legislature's line numbers
            (out / "bills" / f"{b['num']}-text.html").write_text(
                env.get_template("text.html").render(
                    root="../", b=b, version=version, fmt=fmt,
                    lines=doc_lines(d), chars=nchars,
                    heavy=nchars > 250_000, **common), encoding="utf-8")

            # Exactly what the model is asked, reproduced from the same code
            # that asks it. Building this needs no model and no network -- the
            # brief is assembled from the bill text -- so it is safe to publish
            # and stays true whenever the prompt changes.
            # brief.build reads these with .get, and sqlite3.Row has none
            brief = build_brief(dict(b), d, [dict(r) for r in refs])
            (out / "bills" / f"{b['num']}-prompt.html").write_text(
                env.get_template("prompt.html").render(
                    root="../", b=b, ai=ai, system=P.SYSTEM,
                    brief=brief["text"], brief_chars=len(brief["text"]),
                    passages=brief["passages"],
                    total_passages=brief["total_passages"],
                    truncated=brief["truncated"],
                    retries=RETRIES,
                    passes=[{"name": n, "max_tokens": P.BUDGET[n],
                             "schema": json.dumps(P.SCHEMAS[n], indent=2)}
                            for n in P.ORDER],
                    **common), encoding="utf-8")

        area = classify([r["statute"] for r in refs], b["title"] or "")
        _panels, members = split_sponsor(b["sponsor"] or "", committees)
        member = members_by_url.get(b["sponsor_url"] or "")
        html = env.get_template("bill.html").render(
            root="../", b=b, p=prog, path=pathway(prog), refs=refs,
            members=members, sponsor_has_committees=bool(_panels),
            member=member, area=area, area_slug=slug(area),
            shown_provisions=SHOWN_PROVISIONS,
            blocks=blocks, total_blocks=len(all_blocks),
            has_text=loaded is not None,
            total_changes=sum(bk.changed for bk in all_blocks),
            truncated=len(all_blocks) > MAX_BLOCKS, stats=stats,
            analyses=analyses, ai=ai,
            history=history.get(b["num"], []), **common)
        (out / "bills" / f"{b['num']}.html").write_text(html, encoding="utf-8")

        row = {
            "n": b["num"], "l": b["label"], "t": (b["title"] or "")[:120],
            "o": prog.outcome, "d": prog.outcome_label.upper(),
            "a": area or "",
            "s": " ".join(filter(None, [
                b["label"], b["title"], b["sponsor"], b["cosponsors"]])).lower(),
        }
        if b["chapter_law"]:
            row["d"] = f"LAW · {b['chapter_law']}"
        index_rows.append(row)
        by_area.setdefault(area, []).append(dict(b))
        by_area_of[b["num"]] = area
        if b["chapter_law"]:
            enacted.append(b)
        if i % 400 == 0:
            log(f"  {i}/{len(bills)} bill pages")

    if only is not None:
        # Everything below rebuilds the whole tree: the index, its tiles and
        # counts, and 4,000 statute pages. That is a full build's work, and
        # this path exists to be quick. The front page keeps its previous
        # counts until the next full build.
        #
        # The stylesheet is one file and every page depends on it, so it is
        # copied here too -- skipping it left a rebuilt page styled by the
        # previous build's CSS.
        shutil.copy(HERE / "static" / "style.css", out / "style.css")
        return {"bills": len(bills), "statutes": 0, "files": 0, "bytes": 0,
                "outcomes": outcomes, "partial": True}

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
    from .stages import OUTCOME_SHORT
    outcome_cards = [{"key": k, "label": OUTCOME_SHORT[k], "n": outcomes[k]}
                     for k in order if outcomes.get(k)]
    # the pre-rendered table is what a reader without JavaScript sees
    default_outcome = ("became_law" if outcomes.get("became_law")
                       else (outcome_cards[0]["key"] if outcome_cards else "all"))

    (out / "index.html").write_text(env.get_template("index.html").render(
        root="", outcomes=outcome_cards, default_outcome=default_outcome,
        enacted=sorted(enacted, key=lambda b: b["num"]),
        index_rows=index_rows,
        top_statutes=stat_rows[:10], **common), encoding="utf-8")

    # ------------------------------------------------------------ areas
    (out / "area").mkdir(parents=True, exist_ok=True)
    for a in AREAS:
        rows = sorted(by_area.get(a, []), key=lambda b: b["num"])
        (out / "area" / f"{slug(a)}.html").write_text(
            env.get_template("area.html").render(
                root="../", area=a, area_slug=slug(a),
                rows=[{"num": b["num"], "label": b["label"],
                       "title": b["title"],
                       "disposition": (f"LAW · {b['chapter_law']}" if b["chapter_law"]
                                       else (progress[b["num"]].outcome_label.upper()
                                             if b["num"] in progress else ""))}
                      for b in rows],
                **common), encoding="utf-8")

    # --------------------------------------------------------- calendar
    from .calendar import milestones, phase as cal_phase
    hist_rows = [(h["date"], h["action"])
                 for rows in history.values() for h in rows]
    eff = [b["effective_date"] for b in bills
           if b["chapter_law"] and b["effective_date"]]
    m = milestones(hist_rows, eff)
    today = date.fromisoformat(built)
    label, key = cal_phase(today, m)
    fmt = lambda d: d.strftime("%b %-d, %Y").upper()
    (out / "calendar.html").write_text(env.get_template("calendar.html").render(
        root="", today=fmt(today), phase_label=label, phase_key=key,
        closed=key == "recess", action_count=len(hist_rows),
        events=[{"date": fmt(d), "label": t} for d, t in m["events"]],
        effective=[{"date": fmt(x["date"]), "n": x["n"], "big": x["big"]}
                   for x in m["effective"]],
        seasons=[
            {"when": "JAN–MAR · DURING SESSION",
             "what": "A daily alert naming every new bill in the areas you "
                     "chose, with its plain-English summary, and lifecycle "
                     "updates for bills already moving."},
            {"when": "MAR–MAY · GOVERNOR ACTION",
             "what": "Notice when a bill you follow is signed, vetoed, or "
                     "becomes law without a signature."},
            {"when": "JUN–DEC · INTERIM",
             "what": "The weekly digest continues, covering filings for the "
                     "next session as they appear and laws as they take effect."},
        ],
        **common), encoding="utf-8")

    # -------------------------------------------------------- subscribe
    from .areas import AREAS as _A
    specimen = []
    for b in sorted(enacted, key=lambda b: -b["num"])[:3]:
        row = db.execute("SELECT one_line FROM analysis_ai WHERE session=? AND num=?",
                         (session, b["num"])).fetchone()
        specimen.append({"label": b["label"], "title": b["title"],
                         "area": (by_area_of.get(b["num"]) or "GENERAL"),
                         "one_line": row["one_line"] if row else ""})
    (out / "subscribe.html").write_text(env.get_template("subscribe.html").render(
        root="", counties=COUNTIES, specimen=specimen,
        specimen_date=today.strftime("%a %b %-d").upper(), **common),
        encoding="utf-8")

    (out / "about.html").write_text(env.get_template("about.html").render(
        root="", superseded=outcomes.get("superseded", 0), **common),
        encoding="utf-8")

    shutil.copy(HERE / "static" / "style.css", out / "style.css")


    files = sum(1 for _ in out.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    return {"bills": len(bills), "statutes": written_statutes,
            "files": files, "bytes": size, "outcomes": outcomes}
