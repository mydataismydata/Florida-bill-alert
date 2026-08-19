"""SQLite index over the raw cache.

The raw files on disk are the system of record. This database is a derived
index -- it can always be rebuilt by reparsing the cache, offline.
"""
from __future__ import annotations

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

CREATE TABLE IF NOT EXISTS bill_render (
    session TEXT, num INTEGER, version TEXT,
    fmt TEXT, nsegments INTEGER, nchars INTEGER,
    segments TEXT,          -- compact JSON: [[kind,line,page,text], ...]
    PRIMARY KEY (session, num, version)
);

CREATE INDEX IF NOT EXISTS idx_change_bill ON change(session, num);
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
        self.db.commit()

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

    def known_bill_nums(self, session: str) -> set:
        rows = self.db.execute(
            "SELECT num FROM bill WHERE session=?", (session,)).fetchall()
        return {r["num"] for r in rows}

    def commit(self):
        pass          # autocommit: every write lands as it is made
