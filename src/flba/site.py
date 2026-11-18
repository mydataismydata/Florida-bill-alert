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

from .areas import AREAS, classify, slug
from .analysis.analyze import tidy_statute
from .diff import PLAIN, BillDiff, Segment, context_blocks, locate
from .diff import lines as doc_lines
from .stages import OUTCOME_SHORT, kind_of, pathway, track

HERE = Path(__file__).resolve().parent
SITE_NAME = "Know Your Legislation"
REPO = "https://github.com/mydataismydata/Florida-bill-alert"
# "1.0.0 (001)": the release, then the build number. The build number goes up
# by one for every release to the public site and never starts over. The
# release goes up with it when what the site does changes.
VERSION_FILE = HERE.parents[1] / "VERSION"
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

# The 2026 bill pages were published at bills/<num>.html while 2026 was the
# only session, and links to them have gone out in emails and from other
# sites, so they stay where they are. Every later session gets a folder of its
# own, because bill numbers start again each year: bills/2027/<num>.html.
FLAT_SESSION = "2026"


def bill_dir(session: str) -> str:
    """The folder a session's bill pages live in, relative to the site root."""
    return "bills" if session == FLAT_SESSION else f"bills/{session}"


def and_list(items) -> str:
    """"2027", "2027 and 2026", "2028, 2027 and 2026"."""
    items = [str(i) for i in items]
    return (" and ".join(items) if len(items) < 3
            else ", ".join(items[:-1]) + " and " + items[-1])


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(HERE / "templates")),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True, lstrip_blocks=True)
    env.filters["money"] = money
    env.filters["billno"] = short_label
    env.filters["and_list"] = and_list
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


# Built by scripts/make_icons.py from one square source and committed, so a
# build needs no image library. favicon.ico sits at the root because a browser
# asks for it there whether a page names it or not.
ICONS = ("favicon.ico", "icon-192.png", "icon-512.png", "apple-touch-icon.png")


def _copy_static(out: Path) -> None:
    """The stylesheet, the six faces it names, and the icons.

    They travel with the site because no page may reach a font CDN: a reader
    opening a bill is not a reason for a third party to hear about it. Copying
    the stylesheet alone left the CSS asking the host for files it did not
    have, which is six 404s and a page silently in a fallback face.
    """
    shutil.copy(HERE / "static" / "style.css", out / "style.css")
    shutil.copytree(HERE / "static" / "fonts", out / "fonts", dirs_exist_ok=True)
    for name in ICONS:
        shutil.copy(HERE / "static" / name, out / name)


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
                "unclear", "dropped", "flagged", "failures", "stats",
                "corrections"):
        try:
            out[key] = json.loads(out[key] or "[]")
        except Exception:
            out[key] = []
    if not isinstance(out.get("stats"), dict):
        out["stats"] = {}
    return out


# A list of names longer than this shows its first two and a count, and the
# count opens the rest in a slide-out. Three or fewer are shown whole, so the line
# never reads "+ 1 others".
NAMES_SHOWN = 2
NAMES_WHOLE = 3

# How the roll calls group a chamber's members, in the order they are shown.
VOTE_GROUPS = (("Y", "Yeas"), ("N", "Nays"), ("AV", "Abstained"),
               ("NV", "Not voting"))
_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2})\s*([AP]M))?")


# How a party reads beside a name: "Boyd (R)".
PARTY_SHORT = {"Republican": "R", "Democrat": "D", "Democratic": "D",
               "No Party Affiliation": "NPA"}


class Roster:
    """Every seat in both chambers (`flba members`), to put a person behind a
    name on a roll call or a co-sponsor list.

    The Legislature writes a surname, with an initial where two members share
    one ("Alvarez, D."), and a floor roll call adds the district. A name that
    does not lead to exactly one person is left unlinked, because a wrong
    link reads exactly like a right one.
    """

    def __init__(self, db, session: str):
        from .finance import roster_index
        # The roster of that session: an election changes who holds a seat,
        # and a 2026 roll call names the members who sat in 2026.
        rows = ([dict(r) for r in _rows(db, "SELECT * FROM legislator"
                                            " WHERE session=?", session)]
                if _has_table(db, "legislator") else [])
        # The Senate first: a member who moved up from the House is shown
        # where they sit now.
        rows.sort(key=lambda r: (r["chamber"] != "Senate", r["district"]))
        self.rows = rows
        self.index = {c: roster_index([r for r in rows if r["chamber"] == c])
                      for c in ("Senate", "House")}

    def find(self, chamber: str, token: str, district: int | None = None):
        from .finance import fold, match, split_token
        index = self.index.get(chamber) or {}
        if district is None:
            return match(token, index)
        surname, _ = split_token(token)
        rows = [r for r in index.get(fold(surname).lower(), [])
                if r["district"] == district]
        return rows[0] if len(rows) == 1 else None

    def person(self, chamber: str, token: str, district: int | None = None,
               **extra) -> dict:
        """A name as the bill page lists it: the Legislature's own spelling,
        and the party and page of the member it names, where that is certain."""
        who = self.find(chamber, token, district)
        return dict(extra, name=token, district=district,
                    party=PARTY_SHORT.get((who or {}).get("party"),
                                          (who or {}).get("party", "")),
                    slug=member_slug(who["display"]) if who else "")


def names_line(names: list) -> dict:
    """What the truncated line shows of a list, and how many it leaves out."""
    if len(names) <= NAMES_WHOLE:
        return {"shown": list(names), "more": 0}
    return {"shown": list(names[:NAMES_SHOWN]), "more": len(names) - NAMES_SHOWN}


def _when(text: str) -> tuple:
    """A vote's date and time as a sort key, from "2/3/2026 1:00 PM"."""
    m = _DATE.match(text or "")
    if not m:
        return (9999, 0, 0, 0)
    mo, d, y, h, mi, ap = m.groups()
    hour = (int(h) % 12 + (12 if ap == "PM" else 0)) if h else 0
    return (int(y), int(mo), int(d), hour * 60 + int(mi or 0))


def _vote_where(committee: str, url: str) -> str:
    """Where a roll call was taken, from the PDF's own filename."""
    name = url.rsplit("/", 1)[-1]
    if name.startswith("HouseVote"):
        return "House floor"
    if name.startswith("SenateVote"):
        return "Senate floor"
    chamber = "Senate" if "Vote Record" in name else ""
    return " ".join(filter(None, [chamber, "committee"])) + (
        f" · {committee}" if committee else "")


def _member_note(m: dict, layout_floor: bool) -> str:
    if m["after"] and layout_floor:
        return f"voted {'yea' if m['after'] == 'Y' else 'nay'} after the roll call"
    if m["after"]:
        return "voted after the roll call"
    if m["excused"]:
        return "excused"
    return ""


def load_votes(db, session: str, roster: Roster) -> dict[int, list]:
    """Every bill's roll calls, most recent first, with each member's vote.

    The names are there for a roll call `flba votes` has read. One it has not
    -- not fetched yet, or its names did not add up -- shows the totals the
    bill page states and a link to the PDF.
    """
    if not _has_table(db, "roll_call"):
        return {}
    info = {r["url"]: dict(r) for r in
            _rows(db, "SELECT * FROM roll_call_info WHERE session=?", session)}
    members: dict[str, list] = {}
    for r in _rows(db, "SELECT url,name,district,code,excused,after,position"
                       "  FROM roll_call WHERE session=? ORDER BY url,seq",
                   session):
        members.setdefault(r["url"], []).append(dict(r))
    out: dict[int, list] = {}
    for v in _rows(db, "SELECT * FROM vote WHERE session=?", session):
        url = v["url"] or ""
        where = _vote_where(v["committee"] or "", url)
        floor = where.endswith("floor")
        meta = info.get(url) or {}
        chamber = where.split(" ")[0]
        groups = []
        for code, label in VOTE_GROUPS:
            who = [roster.person(chamber, m["name"],
                                 m["district"] if floor else None,
                                 position=m["position"] or "",
                                 note=_member_note(m, floor))
                   for m in members.get(url, []) if m["code"] == code]
            if who:
                groups.append({"code": code, "label": label, "names": who,
                               **names_line(who)})
        out.setdefault(v["num"], []).append({
            "where": where, "date": (v["date"] or "").split(" ")[0],
            "sort": _when(v["date"]), "motion": meta.get("motion") or "",
            "result": v["result"] or "", "url": url, "groups": groups,
            "chamber": chamber, "members": members.get(url, [])})
    for events in out.values():
        events.sort(key=lambda e: e["sort"], reverse=True)
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

    featured = session
    # Every session in the database, newest first. The one asked for is the
    # one the home page opens on. The others stay on the site, each session's
    # bill pages in a folder of its own (bill_dir), and every listing lets a
    # reader switch between them.
    years = sorted({r["session"] for r in
                    _rows(db, "SELECT DISTINCT session FROM bill")} | {featured},
                   reverse=True)
    if only is not None:
        years = [featured]
    bills_by: dict[str, list] = {}
    built_by: dict[str, set] = {}
    for s in years:
        rows = _rows(db, "SELECT * FROM bill WHERE session=? ORDER BY num", s)
        if limit:
            rows = rows[:limit]
        # A partial build must not emit links to pages it never wrote, so
        # every cross-reference below is scoped to the bills actually rendered.
        built_by[s] = {b["num"] for b in rows}
        if only is not None:
            # Refresh one bill over a site that is already complete. The rest
            # of the tree stays on disk, so cross-references may still point
            # at it -- what must not happen is scoping links to the single
            # bill rebuilt.
            rows = [b for b in rows if b["num"] == only]
            if not rows:
                raise SystemExit(f"bill {only} is not in session {s}")
        bills_by[s] = rows
    if not bills_by[featured]:
        raise SystemExit(f"no bills ingested for session {featured}")
    built_keys = {(s, n) for s, nums in built_by.items() for n in nums}

    # Which statutes will actually get a page. The model cites subsections --
    # "1002.33(10)(e)" -- and pages are keyed on the bare number, so a claim's
    # link has to be resolved against this rather than trusted.
    statute_pages = {r["statute"] for r in
                     _rows(db, "SELECT DISTINCT session, statute, num"
                               "  FROM statute_ref")
                     if (r["session"], r["num"]) in built_keys
                     and SAFE_CITE.match(r["statute"])}

    committees = {r["name"] for r in
                  _rows(db, "SELECT DISTINCT name FROM committee_ref")}

    from .calendar import parse as parse_day
    from .finance import donation_links, fold

    # Every bill a member filed, for the page that answers "what else have
    # they put their name to". Keyed by the page's own name, so a member's
    # bills from every session land on one page, the House years and the
    # Senate years of someone who moved up included.
    by_member: dict[str, dict] = {}
    # Each session's roster, newest first, for the members who filed nothing.
    roster_rows: list[dict] = []

    # Who voted which way, inverted for the search box: for each bill, which
    # members voted yea, nay or abstained, by their place in one shared list
    # of names. It is a file of its own because it is several times the size
    # of the bill rows, and only a search that asks about votes needs it.
    voter_ids: dict[str, int] = {}
    vote_index: dict[str, dict] = {}

    release = (VERSION_FILE.read_text().strip()
               if VERSION_FILE.exists() else "")
    common = dict(site_name=SITE_NAME, session=featured, years=years,
                  bill_dirs={s: bill_dir(s) + "/" for s in years},
                  built=built, release=release, repo=REPO,
                  bill_count=sum(len(v) for v in bills_by.values()),
                  local=local,
                  areas=[{"name": a, "slug": slug(a)} for a in AREAS],
                  page="")

    # ---------------------------------------------------------- bill pages
    index_rows, enacted = [], []
    outcomes_by: dict[str, dict] = {}
    history_by: dict[str, dict] = {}
    by_area: dict = {}
    progress: dict[tuple, object] = {}
    for session in years:
        # One pass per session. From here to the end of the loop, `session`
        # is the year being rendered, not the one the home page opens on.
        bills, built_nums = bills_by[session], built_by[session]
        bdir = bill_dir(session)
        (out / bdir).mkdir(parents=True, exist_ok=True)
        broot = "../" * (bdir.count("/") + 1)
        common_s = dict(common, session=session, bdir=bdir)
        outcomes = outcomes_by.setdefault(session, {})

        # Who each sponsor actually is, matched by `flba finance` before the
        # build: full name, district, party, and a link to their filings
        # where there are any. A corpus ingested before this existed has no
        # table at all, and that must not stop a build.
        sponsors = {}
        if _has_table(db, "sponsor_finance"):
            sponsors = {(r["chamber"], r["token"]): dict(r) for r in
                        _rows(db, "SELECT * FROM sponsor_finance"
                                  " WHERE session=? AND member_name<>''", session)}

        history: dict[int, list] = {}
        for r in _rows(db, "SELECT num,date,chamber,action FROM history"
                           " WHERE session=? ORDER BY num,seq", session):
            history.setdefault(r["num"], []).append(dict(r))
        history_by[session] = history

        # Who voted which way on each bill, where the roll calls have been
        # read, each name matched to a member, their party and their page.
        roster = Roster(db, session)
        roster_rows += roster.rows
        votes = load_votes(db, session, roster)

        for i, b in enumerate(bills, 1):
            prog = track(history.get(b["num"], []), b["chapter_law"],
                         kind_of(b["label"]))
            progress[(session, b["num"])] = prog
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
                (out / bdir / f"{b['num']}-text.html").write_text(
                    env.get_template("text.html").render(
                        root=broot, b=b, version=version, fmt=fmt,
                        lines=doc_lines(d), chars=nchars,
                        heavy=nchars > 250_000, **common_s), encoding="utf-8")

            area = classify([r["statute"] for r in refs], b["title"] or "")
            _panels, members = split_sponsor(b["sponsor"] or "", committees)
            # The sponsor row stays the chamber's own wording. Everything we have
            # added to it -- the person's full name, their seat, their filings --
            # belongs on the line that names the people, not the raw field.
            filed_by = [
                dict(sponsors.get((b["chamber"], token)) or {}, token=token)
                for token in members]
            # The day the bill was filed: its first "Filed" action. The donations
            # dated a month either side of it are one of the views of who funds
            # its sponsor.
            filed_on = next((parse_day(h["date"]) for h in history.get(b["num"], [])
                             if (h["action"] or "").strip() == "Filed"), None)
            for f in filed_by:
                # The name leads to their own page here rather than out to the
                # chamber's site: what a reader wants next is the rest of what
                # this person filed, and the official page is one click on from
                # there.
                f["slug"] = member_slug(f["member_name"]) if f.get("member_name") else ""
                f["donations"] = (donation_links(f["url"], f["member_name"],
                                                 session, filed_on)
                                  if f.get("url") else [])
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
            # A companion is only linked when its page was written too. A --only
            # build writes one bill, and a link out of it would land nowhere.
            companion_href = (prog.companion_num
                              if prog.companion_num in built_nums else None)
            cosponsors = [roster.person(b["chamber"], c.strip())
                          for c in (b["cosponsors"] or "").split(";") if c.strip()]
            bill_votes = votes.get(b["num"], [])
            cast: dict[str, set] = {}
            for e in bill_votes:
                for m in e["members"]:
                    if m["code"] in ("Y", "N", "AV"):
                        key = fold(m["name"]).lower()
                        cast.setdefault(m["code"], set()).add(
                            voter_ids.setdefault(key, len(voter_ids)))
            if cast:
                vote_index[f"{session}/{b['num']}"] = {
                    k: sorted(v) for k, v in cast.items()}
            html = env.get_template("bill.html").render(
                root=broot, b=b, p=prog, path=pathway(prog), refs=refs,
                cosponsors=cosponsors, cosponsors_line=names_line(cosponsors),
                votes=bill_votes,
                companion_href=companion_href,
                members=members, sponsor_has_committees=bool(_panels),
                filed_by=filed_by,
                area=area, area_slug=slug(area),
                shown_provisions=SHOWN_PROVISIONS,
                blocks=blocks, total_blocks=len(all_blocks),
                has_text=loaded is not None,
                total_changes=sum(bk.changed for bk in all_blocks),
                truncated=len(all_blocks) > MAX_BLOCKS, stats=stats,
                analyses=analyses, ai=ai,
                history=history.get(b["num"], []), **common_s)
            (out / bdir / f"{b['num']}.html").write_text(html, encoding="utf-8")

            row = {
                "n": b["num"], "y": session, "l": short_label(b["label"]),
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
                stub = f.get("slug")
                if not stub:
                    continue
                # Sessions run newest first, so a page takes its seat, party
                # and links from the latest session the member filed in.
                page = by_member.setdefault(stub, dict(
                    name=f["member_name"], chamber=b["chamber"],
                    district=f.get("district"), party=f.get("party", ""),
                    official=f.get("member_url", ""),
                    donations=f.get("url", ""), session=session, rows=[]))
                if page["name"] != f["member_name"]:
                    # Two people cannot share a page. Nothing in the rosters
                    # so far collides, and if one ever does this says so
                    # instead of quietly publishing one member's bills under
                    # another's name.
                    raise SystemExit(f"two members share the URL {stub}: "
                                     f"{page['name']} and {f['member_name']}")
                page["rows"].append(row)
            by_area.setdefault(area, []).append(row)
            if b["chapter_law"]:
                enacted.append(dict(b, area=area))
            if i % 400 == 0:
                log(f"  {session}: {i}/{len(bills)} bill pages")

    # Back to the session the home page opens on.
    session = featured
    bills, history = bills_by[featured], history_by[featured]
    outcomes = outcomes_by[featured]

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
        return {"bills": len(bills), "bill_pages": len(bills), "statutes": 0,
                "files": 0, "bytes": 0, "outcomes": outcomes, "partial": True}

    (out / "search-index.json").write_text(
        json.dumps(index_rows, separators=(",", ":")), encoding="utf-8")
    (out / "votes-index.json").write_text(
        json.dumps({"m": list(voter_ids), "b": vote_index},
                   separators=(",", ":")), encoding="utf-8")

    # ------------------------------------------------------ statute pages
    # Across every session: a section of law is the same section whichever
    # year's bill amends it.
    refs_all = [r for r in _rows(db,
                    "SELECT r.session, r.statute, r.num, r.action,"
                    "       r.words_added, r.words_deleted, b.label, b.title"
                    "  FROM statute_ref r JOIN bill b"
                    "    ON b.session=r.session AND b.num=r.num")
                if (r["session"], r["num"]) in built_keys]
    agg: dict[str, dict] = {}
    cited: dict[str, dict] = {}
    for r in refs_all:
        a = agg.setdefault(r["statute"], {"statute": r["statute"], "keys": set(),
                                          "added": 0, "deleted": 0})
        a["keys"].add((r["session"], r["num"]))
        a["added"] += r["words_added"] or 0
        a["deleted"] += r["words_deleted"] or 0
        # One row per distinct thing a bill does to the section.
        cited.setdefault(r["statute"], {}).setdefault(
            (r["session"], r["num"], r["label"], r["title"], r["action"],
             r["words_added"], r["words_deleted"]), r)
    stat_rows = sorted(
        ({**a, "bills": len(a["keys"])} for a in agg.values()),
        key=lambda a: (-a["bills"], a["statute"]))

    written_statutes = 0
    for st in stat_rows:
        cite = st["statute"]
        if not SAFE_CITE.match(cite):        # never let a citation escape the dir
            continue
        rows = sorted(cited[cite].values(), key=lambda r: r["num"])
        rows.sort(key=lambda r: r["session"], reverse=True)
        rows = [dict(r, outcome=p.outcome if p else "pending",
                     outcome_label=p.outcome_label if p else "—")
                for r in rows
                for p in [progress.get((r["session"], r["num"]))]]
        html = env.get_template("statute.html").render(
            root="../", cite=cite, rows=rows,
            row_years=sorted({r["session"] for r in rows}, reverse=True),
            **common)
        (out / "statutes" / f"{cite}.html").write_text(html, encoding="utf-8")
        written_statutes += 1

    (out / "statutes" / "index.html").write_text(
        env.get_template("statutes.html").render(root="../", rows=stat_rows,
                                                 **common), encoding="utf-8")

    # ------------------------------------------------------- front and about
    def year_cards(rows, year):
        """The endings a listing can filter by. Every ending any of its years
        holds is offered, so switching year never moves the tiles about, and
        each carries its count for the year on show."""
        seen = {r["o"] for r in rows}
        counts: dict = {}
        for r in rows:
            if r["y"] == year:
                counts[r["o"]] = counts.get(r["o"], 0) + 1
        return [{"key": k, "label": OUTCOME_SHORT[k], "n": counts.get(k, 0)}
                for k in OUTCOME_ORDER if k in seen]

    def newest_first(rows):
        rows = sorted(rows, key=lambda r: r["n"])
        return sorted(rows, key=lambda r: r["y"], reverse=True)

    outcome_cards = year_cards(index_rows, featured)
    # The pre-rendered table is what a reader without JavaScript sees. A
    # session with laws opens on them; one with none yet opens on every bill.
    default_outcome = "became_law" if outcomes.get("became_law") else "all"
    default_noun = next((c["label"].upper() for c in outcome_cards
                         if c["key"] == default_outcome), "ALL BILLS")
    featured_rows = [r for r in index_rows if r["y"] == featured]
    shown = [r for r in featured_rows
             if default_outcome == "all" or r["o"] == default_outcome]

    (out / "index.html").write_text(env.get_template("index.html").render(
        root="", outcomes=outcome_cards, default_outcome=default_outcome,
        default_noun=default_noun, shown=shown, year_count=len(featured_rows),
        index_rows=index_rows, year=featured,
        top_statutes=stat_rows[:10], **common), encoding="utf-8")

    # ------------------------------------------------------------ areas
    (out / "area").mkdir(parents=True, exist_ok=True)
    for a in AREAS:
        rows = newest_first(by_area.get(a, []))
        page_years = sorted({r["y"] for r in rows}, reverse=True) or [featured]
        # Counted over this area, not the session: filtering Healthcare by
        # "vetoed" must offer the number of vetoed healthcare bills or not
        # offer it at all.
        (out / "area" / f"{slug(a)}.html").write_text(
            env.get_template("area.html").render(
                root="../", area=a, area_slug=slug(a), rows=rows,
                shown=[r for r in rows if r["y"] == page_years[0]],
                outcomes=year_cards(rows, page_years[0]),
                page_years=page_years, year=page_years[0],
                **common), encoding="utf-8")

    # ----------------------------------------------------------- members
    # One page per member, so a name on a bill -- sponsor, co-sponsor or
    # voter -- leads to the rest of their record rather than off the site.
    # The page lists the bills they filed in every session, and a member who
    # filed none still has one.
    (out / "member").mkdir(parents=True, exist_ok=True)
    pages = by_member
    for leg in roster_rows:
        stub = member_slug(leg["display"])
        # Already there: they filed bills, they sat in an earlier session's
        # roster too, or they sat in the other chamber first and the roster
        # lists them twice under one name.
        if stub and stub not in pages:
            pages[stub] = dict(name=leg["display"], chamber=leg["chamber"],
                               district=leg["district"], party=leg["party"],
                               official=leg["url"], donations="",
                               session=leg["session"], rows=[])
    for stub, page in sorted(pages.items()):
        rows = newest_first(page.pop("rows"))
        page_years = (sorted({r["y"] for r in rows}, reverse=True)
                      or [page.pop("session")])
        page.pop("session", None)
        (out / "member" / f"{stub}.html").write_text(
            env.get_template("member.html").render(
                root="../", rows=rows,
                shown=[r for r in rows if r["y"] == page_years[0]],
                outcomes=year_cards(rows, page_years[0]),
                enacted=sum(r["o"] == "became_law" for r in rows),
                page_years=page_years, year=page_years[0], **page,
                **common), encoding="utf-8")

    # --------------------------------------------------------- calendar
    from .calendar import (ANNOUNCED, announced, milestones,
                           phase as cal_phase, status as cal_status)
    today = date.fromisoformat(built)
    fmt = lambda d: d.strftime("%b %-d, %Y").upper()

    def span(first, last):
        if not last or last == first:
            return fmt(first)
        if (first.year, first.month) == (last.year, last.month):
            return f"{first:%b} {first.day}–{last.day}, {last.year}".upper()
        if first.year == last.year:
            return f"{first:%b} {first.day}–{last:%b} {last.day}, {last.year}".upper()
        return f"{fmt(first)}–{fmt(last)}"

    def record(s):
        """A session's dated actions and the dates its laws take effect."""
        if s in history_by:
            return ([(h["date"], h["action"])
                     for rows in history_by[s].values() for h in rows],
                    [b["effective_date"] for b in bills_by[s]
                     if b["chapter_law"] and b["effective_date"]])
        return ([(r["date"], r["action"]) for r in
                 _rows(db, "SELECT date, action FROM history WHERE session=?", s)],
                [r["effective_date"] for r in
                 _rows(db, "SELECT effective_date FROM bill WHERE session=?"
                           " AND chapter_law<>'' AND effective_date<>''", s)])

    # Every session the database holds and every one with announced dates,
    # newest first. A new session goes on top and the older ones stay below
    # it. Within each, the latest date comes first.
    sessions = ({r["session"] for r in
                 _rows(db, "SELECT DISTINCT session FROM history")}
                | set(ANNOUNCED) | {session})
    cal_years, m_here = [], None
    for s in sorted(sessions, reverse=True):
        hist_rows, eff = record(s)
        m = milestones(hist_rows, eff) if hist_rows else None
        if s == session:
            m_here = m
        events = [{"first": d, "last": None, "label": t}
                  for d, t in (m["events"] if m else [])] + announced(s, m)
        events.sort(key=lambda e: (e["first"], e["last"] or e["first"]),
                    reverse=True)
        cal_years.append({
            "year": s, "status": cal_status(s, today, m), "here": s == session,
            "events": [{"date": span(e["first"], e["last"]), "label": e["label"]}
                       for e in events],
            "effective": [{"date": fmt(x["date"]), "n": x["n"], "big": x["big"]}
                          for x in reversed(m["effective"] if m else [])],
            "action_count": len(hist_rows), "announced": ANNOUNCED.get(s)})
    label, key = cal_phase(today, m_here or {})
    (out / "calendar.html").write_text(env.get_template("calendar.html").render(
        root="", today=fmt(today), phase_label=label, phase_key=key,
        cal_years=cal_years,
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
    # The latest laws of the latest session that has any.
    for b in sorted(enacted, key=lambda b: (b["session"], b["num"]),
                    reverse=True)[:3]:
        row = db.execute("SELECT one_line FROM analysis_ai WHERE session=? AND num=?",
                         (b["session"], b["num"])).fetchone()
        specimen.append({"label": b["label"], "title": b["title"],
                         "area": (b["area"] or "GENERAL"),
                         "one_line": row["one_line"] if row else ""})
    (out / "subscribe.html").write_text(env.get_template("subscribe.html").render(
        root="", counties=COUNTIES, specimen=specimen,
        specimen_date=today.strftime("%a %b %-d").upper(), **common),
        encoding="utf-8")

    (out / "about.html").write_text(env.get_template("about.html").render(
        root="", **common), encoding="utf-8")

    _copy_static(out)
    _copy_endpoints(out)


    files = sum(1 for _ in out.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    return {"bills": len(bills),
            "bill_pages": sum(len(v) for v in bills_by.values()),
            "statutes": written_statutes,
            "files": files, "bytes": size, "outcomes": outcomes}
