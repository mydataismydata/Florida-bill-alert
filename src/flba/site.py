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
from .stages import OUTCOME_SHORT, kind_of, pathway, track

HERE = Path(__file__).resolve().parent
SITE_NAME = "Session Watch"
REPO = "https://github.com/mydataismydata/Florida-bill-alert"
MAX_BLOCKS = 12           # changed passages on the summary page
# Key provisions are the point of the page, so they are shown. The fold
# only exists for a bill long enough that the list stops being readable,
# and with the schema capped at 8 provisions nothing reaches it today.
SHOWN_PROVISIONS = 10

# Florida's 67, for the subscribe form's optional county field.
COUNTIES = ['Alachua', 'Baker', 'Bay', 'Bradford', 'Brevard', 'Broward', 'Calhoun', 'Charlotte', 'Citrus', 'Clay', 'Collier', 'Columbia', 'DeSoto', 'Dixie', 'Duval', 'Escambia', 'Flagler', 'Franklin', 'Gadsden', 'Gilchrist', 'Glades', 'Gulf', 'Hamilton', 'Hardee', 'Hendry', 'Hernando', 'Highlands', 'Hillsborough', 'Holmes', 'Indian River', 'Jackson', 'Jefferson', 'Lafayette', 'Lake', 'Lee', 'Leon', 'Levy', 'Liberty', 'Madison', 'Manatee', 'Marion', 'Martin', 'Miami-Dade', 'Monroe', 'Nassau', 'Okaloosa', 'Okeechobee', 'Orange', 'Osceola', 'Palm Beach', 'Pasco', 'Pinellas', 'Polk', 'Putnam', 'St. Johns', 'St. Lucie', 'Santa Rosa', 'Sarasota', 'Seminole', 'Sumter', 'Suwannee', 'Taylor', 'Union', 'Volusia', 'Wakulla', 'Walton', 'Washington']

KIND_NAMES = {0: "plain", 1: "insert", 2: "delete"}
# The order a reader thinks in: what passed, what did not, then the endings
# that are rarer than either.
OUTCOME_ORDER = ("became_law", "died", "superseded", "adopted", "vetoed",
                 "pending", "to_ballot")
SAFE_CITE = re.compile(r"^[0-9A-Za-z.]+$")


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(HERE / "templates")),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True, lstrip_blocks=True)
    env.filters["money"] = money
    env.filters["billno"] = short_label
    return env


def money(amount) -> str:
    """A dollar figure at the precision a reader can hold in their head.

    Campaign totals span four orders of magnitude across one session's
    sponsors, and the cents in a filing are noise at every one of them.
    """
    try:
        value = float(amount or 0)
    except (TypeError, ValueError):
        return ""
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}k"
    return f"${value:,.0f}"


# "CS/CS/SB 36" is one bill, SB 36, carried through two committee
# substitutes. The prefix is a fact about its history and says nothing about
# which bill it is, so listings and browser tabs show the number alone.
def short_label(label: str) -> str:
    return (label or "").rsplit("/", 1)[-1].strip()


def member_slug(name: str) -> str:
    """URL form of a member's name: 'Susan Valdés' -> 'susan-valdes'.

    Accents are folded rather than stripped, so a path never carries a stray
    hyphen where a letter should be, and nothing but letters, digits and
    hyphens survives -- a name cannot walk out of the directory it is
    written into.
    """
    from .finance import fold
    return re.sub(r"[^a-z0-9]+", "-", fold(name or "").lower()).strip("-")


def _outcome_cards(counts) -> list[dict]:
    """The dispositions a listing can filter by, in reading order.

    An outcome with no bills behind it is left out rather than offered: a
    filter that empties the table teaches the reader nothing, and an area
    page holds a different set of endings from the session as a whole.
    """
    return [{"key": k, "label": OUTCOME_SHORT[k], "n": counts[k]}
            for k in OUTCOME_ORDER if counts.get(k)]


def _rows(db, sql, *args):
    return db.execute(sql, args).fetchall()


def _has_table(db, name: str) -> bool:
    return bool(db.execute("SELECT 1 FROM sqlite_master"
                           " WHERE type='table' AND name=?", (name,)).fetchone())


def _copy_static(out: Path) -> None:
    """The stylesheet and the six faces it names.

    They travel with the site because no page may reach a font CDN: a reader
    opening a bill is not a reason for a third party to hear about it. Copying
    the stylesheet alone left the CSS asking the host for files it did not
    have, which is six 404s and a page silently in a fallback face.
    """
    shutil.copy(HERE / "static" / "style.css", out / "style.css")
    shutil.copytree(HERE / "static" / "fonts", out / "fonts", dirs_exist_ok=True)


def _copy_endpoints(out: Path) -> None:
    """Ship the subscribe endpoints with the bundle.

    They travel in the same tree as the HTML so one deploy moves both and
    --delete cannot strip them. config.php is not among them: it holds the
    database password and is uploaded once, by hand, and never rebuilt.
    """
    src = HERE.parent.parent / "public"
    if not src.is_dir():
        return
    # config.php holds the database password. config.example.php and schema.sql
    # are for whoever sets the server up, and a web root would serve schema.sql
    # as a plain file to anyone who asked.
    skip = {"config.php", "config.example.php"}
    for php in sorted(src.glob("*.php")):
        if php.name not in skip:
            shutil.copy(php, out / php.name)


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
    # Who each sponsor actually is, matched by `flba finance` before the
    # build: full name, district, party, and a link to their filings where
    # there are any. A corpus ingested before this existed has no table at
    # all, and that must not stop a build.
    sponsors = {}
    if _has_table(db, "sponsor_finance"):
        sponsors = {(r["chamber"], r["token"]): dict(r) for r in
                    _rows(db, "SELECT * FROM sponsor_finance"
                              " WHERE session=? AND member_name<>''", session)}

    # Every bill a member filed, for the page that answers "what else have
    # they put their name to". Keyed the way a sponsor token is: one person
    # per chamber.
    by_member: dict[tuple, list] = {}

    history: dict[int, list] = {}
    for r in _rows(db, "SELECT num,date,chamber,action FROM history"
                       " WHERE session=? ORDER BY num,seq", session):
        history.setdefault(r["num"], []).append(dict(r))

    common = dict(site_name=SITE_NAME, session=session, built=built,
                  repo=REPO, bill_count=len(bills), local=local,
                  areas=[{"name": a, "slug": slug(a)} for a in AREAS],
                  page="")

    # ---------------------------------------------------------- bill pages
    index_rows, outcomes, enacted, enacted_rows = [], {}, [], []
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
        # The sponsor row stays the chamber's own wording. Everything we have
        # added to it -- the person's full name, their seat, their filings --
        # belongs on the line that names the people, not the raw field.
        filed_by = [
            dict(sponsors.get((b["chamber"], token)) or {}, token=token)
            for token in members]
        for f in filed_by:
            # The name leads to their own page here rather than out to the
            # chamber's site: what a reader wants next is the rest of what
            # this person filed, and the official page is one click on from
            # there.
            f["slug"] = member_slug(f["member_name"]) if f.get("member_name") else ""
        # Who the listing names, and where each of them has a page. Taken
        # before the row below is thinned out, and paired rather than
        # derived: a committee has no page, and a slug guessed from a name
        # that has none is a 404 that looks exactly like a link.
        filed_who = ([[f.get("member_name") or f["token"], f.get("slug", "")]
                      for f in filed_by] or [[p, ""] for p in _panels])
        filed_names = [name for name, _ in filed_who]
        # What that column sorts on. It reads "Ana Maria Rodriguez" and a
        # reader looking for her is looking under R, so it sorts by surname --
        # which the sponsor token already is, rather than the last word of a
        # name, which it only usually is and which a committee has none of.
        filed_key = ("; ".join(f["token"] for f in filed_by)
                     or "; ".join(_panels)).lower()
        # A row that repeats the sponsor field word for word is noise. It
        # earns its place by naming the person a committee chain hides, or by
        # carrying a seat, a party or a link the field above does not.
        if not (_panels or any(f.get("district") or f.get("url") or f.get("slug")
                               for f in filed_by)):
            filed_by = []
        html = env.get_template("bill.html").render(
            root="../", b=b, p=prog, path=pathway(prog), refs=refs,
            members=members, sponsor_has_committees=bool(_panels),
            filed_by=filed_by,
            area=area, area_slug=slug(area),
            shown_provisions=SHOWN_PROVISIONS,
            blocks=blocks, total_blocks=len(all_blocks),
            has_text=loaded is not None,
            total_changes=sum(bk.changed for bk in all_blocks),
            truncated=len(all_blocks) > MAX_BLOCKS, stats=stats,
            analyses=analyses, ai=ai,
            history=history.get(b["num"], []), **common)
        (out / "bills" / f"{b['num']}.html").write_text(html, encoding="utf-8")

        row = {
            "n": b["num"], "l": short_label(b["label"]),
            "t": (b["title"] or "")[:120], "m": filed_who, "k": filed_key,
            "o": prog.outcome, "d": prog.outcome_label.upper(),
            "a": area or "",
            # The number as the Legislature writes it stays searchable even
            # though the short one is shown, so a pasted "CS/CS/SB 36" still
            # finds the bill. The names are here because the sponsor field
            # holds surnames and a reader looking for a member has a person.
            "s": " ".join(filter(None, [b["label"], b["title"], b["sponsor"],
                                        b["cosponsors"]] + filed_names)).lower(),
        }
        if b["chapter_law"]:
            row["d"] = f"LAW · {b['chapter_law']}"
        index_rows.append(row)
        for f in filed_by:
            if f.get("member_name"):
                by_member.setdefault(
                    (b["chamber"], f["token"], f["member_name"]), []).append(row)
        by_area.setdefault(area, []).append(row)
        by_area_of[b["num"]] = area
        if b["chapter_law"]:
            enacted.append(b)
            enacted_rows.append(row)
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
        _copy_static(out)
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
    outcome_cards = _outcome_cards(outcomes)
    # the pre-rendered table is what a reader without JavaScript sees
    default_outcome = ("became_law" if outcomes.get("became_law")
                       else (outcome_cards[0]["key"] if outcome_cards else "all"))
    default_noun = next((c["label"].upper() for c in outcome_cards
                         if c["key"] == default_outcome), "ALL BILLS")

    (out / "index.html").write_text(env.get_template("index.html").render(
        root="", outcomes=outcome_cards, default_outcome=default_outcome,
        default_noun=default_noun, enacted=enacted_rows,
        index_rows=index_rows,
        top_statutes=stat_rows[:10], **common), encoding="utf-8")

    # ------------------------------------------------------------ areas
    (out / "area").mkdir(parents=True, exist_ok=True)
    for a in AREAS:
        rows = sorted(by_area.get(a, []), key=lambda r: r["n"])
        # Counted over this area, not the session: filtering Healthcare by
        # "vetoed" must offer the number of vetoed healthcare bills or not
        # offer it at all.
        counts: dict = {}
        for r in rows:
            counts[r["o"]] = counts.get(r["o"], 0) + 1
        (out / "area" / f"{slug(a)}.html").write_text(
            env.get_template("area.html").render(
                root="../", area=a, area_slug=slug(a), rows=rows,
                outcomes=_outcome_cards(counts),
                **common), encoding="utf-8")

    # ----------------------------------------------------------- members
    # One page per person who filed something, so the name on a bill leads to
    # the rest of their record rather than off the site.
    (out / "member").mkdir(parents=True, exist_ok=True)
    seen_slugs: dict[str, tuple] = {}
    for (chamber, token, name), rows in sorted(by_member.items()):
        who = sponsors.get((chamber, token)) or {}
        stub = member_slug(name)
        if not stub:
            continue
        if stub in seen_slugs:
            # Two people cannot share a page. Nothing in the 2026 rosters
            # collides, and if a session ever does this says so instead of
            # quietly publishing one member's bills under another's name.
            raise SystemExit(f"two members share the URL {stub}: "
                             f"{seen_slugs[stub]} and {(chamber, token)}")
        seen_slugs[stub] = (chamber, token)
        rows = sorted(rows, key=lambda r: r["n"])
        counts: dict = {}
        for r in rows:
            counts[r["o"]] = counts.get(r["o"], 0) + 1
        (out / "member" / f"{stub}.html").write_text(
            env.get_template("member.html").render(
                root="../", name=name, chamber=chamber,
                district=who.get("district"), party=who.get("party", ""),
                official=who.get("member_url", ""), donations=who.get("url", ""),
                rows=rows, outcomes=_outcome_cards(counts),
                enacted=counts.get("became_law", 0),
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

    analysed = db.execute(
        "SELECT COUNT(*) n FROM analysis_ai WHERE session=?", (session,)).fetchone()["n"]
    (out / "about.html").write_text(env.get_template("about.html").render(
        root="", superseded=outcomes.get("superseded", 0), analysed=analysed,
        **common), encoding="utf-8")

    _copy_static(out)
    _copy_endpoints(out)


    files = sum(1 for _ in out.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    return {"bills": len(bills), "statutes": written_statutes,
            "files": files, "bytes": size, "outcomes": outcomes}
