"""SQLite index over the raw cache.

The raw files on disk are the system of record. This database is a derived
index -- it can always be rebuilt by reparsing the cache, offline.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch (
    url          TEXT PRIMARY KEY,
    path         TEXT NOT NULL,
    status       INTEGER,
    content_type TEXT,
    nbytes       INTEGER,
    sha256       TEXT,
    fetched_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bill (
    session      TEXT NOT NULL,
    num          INTEGER NOT NULL,      -- the number in the URL path
    label        TEXT,                  -- 'SB 2' / 'HB 1001'
    chamber      TEXT,
    title        TEXT,
    long_title   TEXT,
    subject      TEXT,                  -- e.g. 'CLAIM/GENERAL'
    sponsor      TEXT,
    cosponsors   TEXT,
    sponsor_url  TEXT,
    chapter_law  TEXT,
    chapter_law_url TEXT,
    effective_date TEXT,
    last_action  TEXT,
    url          TEXT,
    parsed_at    TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (session, num)
);

CREATE TABLE IF NOT EXISTS history (
    session TEXT, num INTEGER, seq INTEGER,
    date TEXT, chamber TEXT, action TEXT,
    PRIMARY KEY (session, num, seq)
);

CREATE TABLE IF NOT EXISTS committee_ref (
    session TEXT, num INTEGER, seq INTEGER,
    chamber TEXT, name TEXT, code TEXT,
    PRIMARY KEY (session, num, chamber, seq)
);

CREATE TABLE IF NOT EXISTS bill_text (
    session TEXT, num INTEGER, version TEXT, posted TEXT,
    fmt TEXT, url TEXT,
    PRIMARY KEY (session, num, version, fmt)
);

CREATE TABLE IF NOT EXISTS analysis (
    session TEXT, num INTEGER, kind TEXT, analysis TEXT,
    author TEXT, posted TEXT, url TEXT,
    PRIMARY KEY (session, num, url)
);

CREATE TABLE IF NOT EXISTS vote (
    session TEXT, num INTEGER, scope TEXT, version TEXT,
    committee TEXT, date TEXT, result TEXT, url TEXT,
    PRIMARY KEY (session, num, url)
);

CREATE TABLE IF NOT EXISTS amendment (
    session TEXT, num INTEGER, scope TEXT, version TEXT, number TEXT,
    kind TEXT, description TEXT, filed_by TEXT, posted TEXT,
    action TEXT, url TEXT,
    PRIMARY KEY (session, num, scope, number)
);

CREATE TABLE IF NOT EXISTS citation (
    session TEXT, num INTEGER, kind TEXT, cite TEXT, url TEXT,
    PRIMARY KEY (session, num, kind, cite)
);

CREATE TABLE IF NOT EXISTS related (
    session TEXT, num INTEGER, label TEXT, subject TEXT,
    filed_by TEXT, relationship TEXT, status TEXT, url TEXT,
    PRIMARY KEY (session, num, label, relationship)
);

CREATE TABLE IF NOT EXISTS statute_ref (
    session TEXT, num INTEGER, version TEXT,
    statute TEXT, action TEXT, bill_section INTEGER, scope TEXT, verb TEXT,
    line_start INTEGER, line_end INTEGER,
    words_added INTEGER, words_deleted INTEGER,
    in_title INTEGER, in_body INTEGER,
    PRIMARY KEY (session, num, version, statute, bill_section)
);

CREATE TABLE IF NOT EXISTS change (
    session TEXT, num INTEGER, version TEXT, seq INTEGER,
    kind TEXT, line INTEGER, page INTEGER, text TEXT,
    PRIMARY KEY (session, num, version, seq)
);

-- Kept per session: an election changes who holds a seat, and the earlier
-- session's bills must still name the member who held it then.
CREATE TABLE IF NOT EXISTS member (
    session TEXT NOT NULL DEFAULT '',
    chamber TEXT, district INTEGER, name TEXT, party TEXT, url TEXT,
    -- What they are called, where the chamber publishes it. Kept apart from
    -- `name` because pages should read "Thomas J. Leek" while a search of the
    -- campaign filings has to know to also try "Tom".
    nickname TEXT DEFAULT '',
    fetched_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (session, chamber, district)
);

-- One row per sponsor name as the Legislature writes it, so the site can
-- look up a link without re-running the match. Keyed on the raw token
-- because that is what a bill actually carries: districts are not unique in
-- the House, where seven seats changed hands mid-session.
CREATE TABLE IF NOT EXISTS sponsor_finance (
    session TEXT, chamber TEXT, token TEXT,
    member_name TEXT,       -- as the chamber's roster writes it
    district INTEGER, party TEXT,
    person_name TEXT,       -- as the filings write it, '' when unmatched
    url TEXT,               -- '' when there is no filing to link to
    member_url TEXT DEFAULT '',   -- their page on the chamber's own site
    total_received TEXT, total_given TEXT,
    filings INTEGER, same_surname INTEGER,
    checked_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (session, chamber, token)
);

CREATE TABLE IF NOT EXISTS bill_render (
    session TEXT, num INTEGER, version TEXT,
    fmt TEXT, nsegments INTEGER, nchars INTEGER,
    segments TEXT,          -- compact JSON: [[kind,line,page,text], ...]
    PRIMARY KEY (session, num, version)
);

CREATE TABLE IF NOT EXISTS analysis_ai (
    session TEXT, num INTEGER, version TEXT,
    model TEXT, generated_at TEXT DEFAULT (datetime('now')),
    one_line TEXT, summary TEXT, who_is_affected TEXT,
    reading TEXT, provisions TEXT, implications TEXT, unclear TEXT,
    dropped TEXT, flagged TEXT, failures TEXT, stats TEXT,
    corrections TEXT DEFAULT '[]',
    PRIMARY KEY (session, num)
);

-- Each member's vote on one roll call, read from the PDF the vote row links
-- to (sources/rollcall.py). Keyed on that link, so re-pulling a bill page,
-- which rewrites its vote rows, leaves these valid. code is Y, N, AV
-- (abstain) or NV (no vote recorded). `after` is a vote cast after the roll
-- call: on a floor record it is outside the tally, on a committee record
-- ("VA") it is inside it.
CREATE TABLE IF NOT EXISTS roll_call (
    session TEXT, num INTEGER, url TEXT, seq INTEGER,
    name TEXT, district INTEGER, code TEXT, excused INTEGER, after TEXT,
    position TEXT DEFAULT '',      -- 'President', 'Chair', 'Vice Chair'
    PRIMARY KEY (session, num, url, seq)
);

-- Every seat in both chambers, from each chamber's roster, so a name on a
-- roll call or a co-sponsor list can be matched to a person, a party and a
-- page. `member` holds only the senators who sponsored something. The House
-- roster keeps a member who left mid-term beside the one who replaced them,
-- so the key is the member's own page, not the district. Kept per session,
-- like `member`.
CREATE TABLE IF NOT EXISTS legislator (
    session TEXT NOT NULL DEFAULT '',
    chamber TEXT, url TEXT,
    name TEXT,          -- as the roster writes it: 'Leek, Thomas J. "Tom"'
    display TEXT,       -- as the site shows it, and as its page is named
    district INTEGER, party TEXT,
    PRIMARY KEY (session, chamber, url)
);

-- What each roll call was on ("Passage · Third Reading", "Favorable") and
-- the totals its PDF prints. A chamber can vote on one bill more than once.
CREATE TABLE IF NOT EXISTS roll_call_info (
    session TEXT, num INTEGER, url TEXT,
    motion TEXT, yeas INTEGER, nays INTEGER, not_voting INTEGER,
    PRIMARY KEY (session, num, url)
);

CREATE INDEX IF NOT EXISTS idx_change_bill ON change(session, num);
CREATE INDEX IF NOT EXISTS idx_roll_call_bill ON roll_call(session, num);
CREATE INDEX IF NOT EXISTS idx_statute ON statute_ref(session, statute);
CREATE INDEX IF NOT EXISTS idx_bill_session ON bill(session);
CREATE INDEX IF NOT EXISTS idx_hist_bill    ON history(session, num);
"""


class Store:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # An ad-hoc single-bill pull may run alongside a scheduled backfill.
        # WAL lets them read concurrently; busy_timeout makes the second writer
        # wait its turn instead of failing outright.
        # isolation_level=None -> autocommit. Without it, Python holds a write
        # transaction open across a whole batch of fetches (~100s), which no
        # busy_timeout can outwait.
        self.db = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript(SCHEMA)
        self._add_missing_columns()
        self._rosters_by_session()

    # CREATE TABLE IF NOT EXISTS leaves an existing table alone, so a column
    # added later never reaches a database that predates it.
    _ADDED = (("member", "nickname", "TEXT DEFAULT ''"),
              ("sponsor_finance", "member_url", "TEXT DEFAULT ''"),
              ("analysis_ai", "corrections", "TEXT DEFAULT '[]'"),
              ("roll_call", "position", "TEXT DEFAULT ''"),
              # The last action as the session's bill LIST words it, which
              # is not how the bill's own page words it ("S Filed" there,
              # "Senate - Filed" here). `bills --changed` compares a fresh
              # list against this to find the bills that moved.
              ("bill", "list_action", "TEXT DEFAULT ''"))

    def _add_missing_columns(self) -> None:
        for table, column, decl in self._ADDED:
            have = {r["name"] for r in
                    self.db.execute(f"PRAGMA table_info({table})")}
            if have and column not in have:
                self.db.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        self.db.commit()

    def _rosters_by_session(self) -> None:
        """Rebuild `member` and `legislator` with the session in their key.

        Both were once keyed without it, so a later session's roster would
        have written over an earlier one's. A new column cannot join a
        primary key, so each table is copied into the new shape. The rows
        already there are given the database's earliest session, which is
        the one they were read for.
        """
        for table in ("member", "legislator"):
            cols = [r["name"] for r in
                    self.db.execute(f"PRAGMA table_info({table})")]
            if not cols or "session" in cols:
                continue
            ddl = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \(.*?\n\);",
                            SCHEMA, re.S).group(0)
            first = (self.db.execute("SELECT MIN(session) FROM bill")
                     .fetchone()[0] or "")
            listed = ", ".join(cols)
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(f"ALTER TABLE {table} RENAME TO {table}_unsessioned")
            self.db.execute(ddl)
            self.db.execute(
                f"INSERT INTO {table} (session, {listed})"
                f" SELECT ?, {listed} FROM {table}_unsessioned", (first,))
            self.db.execute(f"DROP TABLE {table}_unsessioned")
            self.db.execute("COMMIT")

    def log_fetch(self, r) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO fetch (url,path,status,content_type,nbytes,sha256)"
            " VALUES (?,?,?,?,?,?)",
            (r.url, str(r.path), r.status, r.content_type, r.nbytes, r.sha256),
        )

    def save_bill(self, rec: dict) -> None:
        s, n = rec["session"], rec["num"]
        self.db.execute("BEGIN IMMEDIATE")   # one short, atomic write
        self.db.execute(
            "INSERT OR REPLACE INTO bill"
            " (session,num,label,chamber,title,long_title,subject,sponsor,"
            "  cosponsors,sponsor_url,chapter_law,chapter_law_url,"
            "  effective_date,last_action,url)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s, n, rec.get("label"), rec.get("chamber"), rec.get("title"),
             rec.get("long_title"), rec.get("subject"), rec.get("sponsor"),
             rec.get("cosponsors"), rec.get("sponsor_url"),
             rec.get("chapter_law"), rec.get("chapter_law_url"),
             rec.get("effective_date"), rec.get("last_action"), rec.get("url")),
        )
        wipe = ("history", "committee_ref", "bill_text", "analysis",
                "vote", "amendment", "citation", "related")
        for tbl in wipe:
            self.db.execute(f"DELETE FROM {tbl} WHERE session=? AND num=?", (s, n))

        for i, h in enumerate(rec.get("history", [])):
            self.db.execute(
                "INSERT OR REPLACE INTO history VALUES (?,?,?,?,?,?)",
                (s, n, i, h["date"], h["chamber"], h["action"]))
        for c in rec.get("committee_refs", []):
            self.db.execute(
                "INSERT OR REPLACE INTO committee_ref VALUES (?,?,?,?,?,?)",
                (s, n, c["seq"], c["chamber"], c["name"], c["code"]))
        for t in rec.get("texts", []):
            self.db.execute(
                "INSERT OR REPLACE INTO bill_text VALUES (?,?,?,?,?,?)",
                (s, n, t["version"], t["posted"], t["fmt"], t["url"]))
        for a in rec.get("analyses", []):
            self.db.execute(
                "INSERT OR REPLACE INTO analysis VALUES (?,?,?,?,?,?,?)",
                (s, n, a["kind"], a["analysis"], a["author"], a["posted"], a["url"]))
        for v in rec.get("votes", []):
            self.db.execute(
                "INSERT OR REPLACE INTO vote VALUES (?,?,?,?,?,?,?,?)",
                (s, n, v["scope"], v["version"], v["committee"], v["date"],
                 v["result"], v["url"]))
        for am in rec.get("amendments", []):
            self.db.execute(
                "INSERT OR REPLACE INTO amendment VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (s, n, am["scope"], am["version"], am["number"], am["kind"],
                 am["description"], am["filed_by"], am["posted"],
                 am["action"], am["url"]))
        for c in rec.get("citations", []):
            self.db.execute(
                "INSERT OR REPLACE INTO citation VALUES (?,?,?,?,?)",
                (s, n, c["kind"], c["cite"], c["url"]))
        for r in rec.get("related", []):
            self.db.execute(
                "INSERT OR REPLACE INTO related VALUES (?,?,?,?,?,?,?,?)",
                (s, n, r["label"], r["subject"], r["filed_by"],
                 r["relationship"], r["status"], r["url"]))
        self.db.execute("COMMIT")

    def save_roll_call(self, session, num, url, got) -> None:
        members, printed = got["members"], got["printed"]
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute(
            "INSERT OR REPLACE INTO roll_call_info VALUES (?,?,?,?,?,?,?)",
            (session, num, url, got["motion"], printed.get("yeas"),
             printed.get("nays"), printed.get("not_voting")))
        self.db.execute("DELETE FROM roll_call WHERE session=? AND num=? AND url=?",
                        (session, num, url))
        for i, m in enumerate(members):
            self.db.execute(
                "INSERT INTO roll_call (session,num,url,seq,name,district,code,"
                " excused,after,position) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (session, num, url, i, m["name"], m["district"], m["code"],
                 int(bool(m["excused"])), m["after"], m.get("position", "")))
        self.db.execute("COMMIT")

    def save_legislators(self, session: str, chamber: str,
                         rows: list[dict]) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("DELETE FROM legislator WHERE session=? AND chamber=?",
                        (session, chamber))
        for r in rows:
            self.db.execute(
                "INSERT OR REPLACE INTO legislator"
                " (session,chamber,url,name,display,district,party)"
                " VALUES (?,?,?,?,?,?,?)",
                (session, chamber, r["url"], r["name"], r["display"],
                 r["district"], r["party"]))
        self.db.execute("COMMIT")

    def save_statute_refs(self, session, num, version, refs) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute(
            "DELETE FROM statute_ref WHERE session=? AND num=? AND version=?",
            (session, num, version))
        for r in refs:
            self.db.execute(
                "INSERT OR REPLACE INTO statute_ref VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (session, num, version, r.statute, r.action, r.bill_section,
                 r.scope, r.verb, r.line_start, r.line_end, r.words_added,
                 r.words_deleted, int(r.in_title), int(r.in_body)))
        self.db.execute("COMMIT")

    def save_member(self, session, chamber, district, name, party, url,
                    nickname: str = "") -> None:
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute(
            "INSERT OR REPLACE INTO member"
            " (session,chamber,district,name,party,url,nickname)"
            " VALUES (?,?,?,?,?,?,?)",
            (session, chamber, district, name, party, url, nickname))
        self.db.execute("COMMIT")

    def save_sponsor_finance(self, session, chamber, token, rec) -> None:
        """Record what a sponsor token resolved to, including a miss.

        A miss is stored rather than skipped so a rebuild does not ask PAC
        Tracker the same unanswerable question again.
        """
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute(
            "INSERT OR REPLACE INTO sponsor_finance"
            " (session,chamber,token,member_name,district,party,person_name,"
            "  url,member_url,total_received,total_given,filings,same_surname,"
            "  checked_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (session, chamber, token, rec.get("member_name", ""),
             rec.get("district"), rec.get("party", ""),
             rec.get("person_name", ""), rec.get("url", ""),
             rec.get("member_url", ""),
             rec.get("total_received", ""), rec.get("total_given", ""),
             rec.get("filings", 0), rec.get("same_surname", 0)))
        self.db.execute("COMMIT")

    def save_render(self, session, num, version, fmt, segments) -> None:
        """Keep the parsed document so the site can show a change inside its
        sentence, and render the whole bill, without re-parsing a 300-page PDF
        on every build."""
        import json
        kinds = {"plain": 0, "insert": 1, "delete": 2}
        packed = [[kinds.get(s.kind, 0), s.line, s.page, s.text]
                  for s in segments]
        blob = json.dumps(packed, separators=(",", ":"), ensure_ascii=False)
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute(
            "INSERT OR REPLACE INTO bill_render VALUES (?,?,?,?,?,?,?)",
            (session, num, version, fmt, len(segments),
             sum(len(s.text) for s in segments), blob))
        self.db.execute("COMMIT")

    def load_render(self, session, num):
        import json
        row = self.db.execute(
            "SELECT version,fmt,segments FROM bill_render"
            " WHERE session=? AND num=?", (session, num)).fetchone()
        if not row:
            return None
        names = {0: "plain", 1: "insert", 2: "delete"}
        from .diff import BillDiff, Segment
        segs = [Segment(names[k], t, ln, pg)
                for k, ln, pg, t in json.loads(row["segments"])]
        return row["version"], row["fmt"], BillDiff(row["fmt"], segs)

    def save_changes(self, session, num, version, segments) -> None:
        """Persist the additions and deletions so the site build need not
        re-parse a 300-page PDF for every rebuild."""
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute(
            "DELETE FROM change WHERE session=? AND num=? AND version=?",
            (session, num, version))
        for i, seg in enumerate(segments):
            self.db.execute(
                "INSERT OR REPLACE INTO change VALUES (?,?,?,?,?,?,?,?)",
                (session, num, version, i, seg.kind, seg.line, seg.page,
                 seg.text[:600]))
        self.db.execute("COMMIT")

    def save_analysis(self, a) -> None:
        import json as _json
        j = lambda v: _json.dumps(v, ensure_ascii=False)
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute(
            "INSERT OR REPLACE INTO analysis_ai"
            " (session,num,version,model,one_line,summary,who_is_affected,"
            "  reading,provisions,implications,unclear,dropped,flagged,"
            "  failures,stats,corrections)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (a.session, a.num, a.version, a.model,
             a.summary.get("one_line", ""),
             j(a.summary.get("summary", [])),
             j(a.summary.get("who_is_affected", [])), a.reading,
             j(a.provisions), j(a.implications), j(a.unclear),
             j(a.dropped), j(a.flagged), j(a.failures), j(a.stats),
             j(getattr(a, "corrections", []))))
        self.db.execute("COMMIT")

    def load_analysis(self, session: str, num: int):
        import json as _json
        row = self.db.execute(
            "SELECT * FROM analysis_ai WHERE session=? AND num=?",
            (session, num)).fetchone()
        if not row:
            return None
        out = dict(row)
        for k in ("summary", "who_is_affected", "provisions", "implications",
                  "unclear", "dropped", "flagged", "failures", "stats",
                  "corrections"):
            try:
                out[k] = _json.loads(out[k] or "[]")
            except Exception:
                out[k] = []
        return out

    def analysed_nums(self, session: str) -> set:
        return {r["num"] for r in self.db.execute(
            "SELECT num FROM analysis_ai WHERE session=?", (session,))}

    def known_bill_nums(self, session: str) -> set:
        rows = self.db.execute(
            "SELECT num FROM bill WHERE session=?", (session,)).fetchall()
        return {r["num"] for r in rows}

    def commit(self):
        pass          # autocommit: every write lands as it is made
