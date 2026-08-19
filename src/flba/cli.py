"""Command line interface for the Florida Bill Alert ingest pipeline.

    python -m flba enumerate --session 2026
    python -m flba bills     --session 2026
    python -m flba docs      --session 2026
    python -m flba status    --session 2026
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from .http import PoliteFetcher
from .sources import flsenate
from .store import Store

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


def _paths(session: str):
    return (PoliteFetcher(DATA / "raw" / "flsenate"),
            Store(DATA / "index.sqlite"),
            DATA / "index" / f"{session}.json")


def is_dead(last_action: str) -> bool:
    """Whether a bill's last recorded action ends it.

    Delegates to the validated rules in `stages`. A substring match on
    "withdrawn" or "laid on table" looks equivalent and is badly wrong: both
    are usually procedural, and treating them as terminal marks bills that
    became law as dead. See stages.py for the evidence.
    """
    from .stages import is_terminal
    action = re.sub(r"^\d[\d/]*\s+\w*\s*-\s*", "", (last_action or "").strip())
    return is_terminal(action)


ORDER_SQL = {
    # most-worked bills first: versions + analyses + votes + amendments
    "activity": """
        SELECT b.num FROM bill b LEFT JOIN (
            SELECT session,num,COUNT(*) n FROM bill_text GROUP BY session,num) t
              ON t.session=b.session AND t.num=b.num
        LEFT JOIN (SELECT session,num,COUNT(*) n FROM analysis GROUP BY session,num) a
              ON a.session=b.session AND a.num=b.num
        LEFT JOIN (SELECT session,num,COUNT(*) n FROM vote GROUP BY session,num) v
              ON v.session=b.session AND v.num=b.num
        WHERE b.session=?
        ORDER BY (COALESCE(t.n,0)+COALESCE(a.n,0)+COALESCE(v.n,0)) DESC, b.num
    """,
    "number": "SELECT num FROM bill WHERE session=? ORDER BY num",
}


class Progress:
    def __init__(self, total: int, label: str, every: int = 25):
        self.total, self.label, self.every = total, label, every
        self.n, self.t0 = 0, time.monotonic()

    def tick(self, note: str = "") -> None:
        self.n += 1
        if self.n % self.every and self.n != self.total:
            return
        el = time.monotonic() - self.t0
        rate = self.n / el if el else 0
        eta = (self.total - self.n) / rate if rate else 0
        print(f"  [{self.label}] {self.n}/{self.total} "
              f"({100*self.n/self.total:.1f}%) {rate:.2f}/s "
              f"eta {eta/60:.1f}m {note}", flush=True)


def cmd_enumerate(args) -> int:
    fetcher, store, index_path = _paths(args.session)
    rows, page, total = [], 1, None

    while True:
        url = flsenate.list_page_url(args.session, page)
        res = fetcher.fetch(url, force=args.refresh)
        store.log_fetch(res)
        if not res.ok:
            print(f"  list page {page}: HTTP {res.status} -- stopping", flush=True)
            break
        page_rows, reported = flsenate.parse_bill_list(res.text())
        if total is None and reported:
            total = reported
            print(f"  site reports {total} bills for session {args.session}", flush=True)
        if not page_rows:
            break
        rows.extend(page_rows)
        print(f"  page {page}: +{len(page_rows)} (running {len(rows)})", flush=True)
        if total and len(rows) >= total:
            break
        page += 1
        if page > 200:
            print("  safety stop at page 200", flush=True)
            break

    seen, uniq = set(), []
    for r in rows:
        if r["num"] is not None and r["num"] not in seen:
            seen.add(r["num"])
            uniq.append(r)

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(
        {"session": args.session, "reported_total": total,
         "scraped_total": len(uniq), "bills": uniq}, indent=1))
    store.commit()

    print(f"\nenumerate: {len(uniq)} unique bills "
          f"(site reported {total}) -> {index_path}")
    if total and len(uniq) != total:
        print(f"  WARNING: count mismatch, missing {total - len(uniq)}")
        return 1
    return 0


def cmd_bills(args) -> int:
    fetcher, store, index_path = _paths(args.session)
    if not index_path.exists():
        print("run `enumerate` first", file=sys.stderr)
        return 2

    bills = json.loads(index_path.read_text())["bills"]
    if args.order == "live":
        bills.sort(key=lambda b: (is_dead(b.get("last_action", "")), b["num"]))
    if args.limit:
        bills = bills[:args.limit]
    done = set() if args.refresh else store.known_bill_nums(args.session)
    todo = [b for b in bills if b["num"] not in done]
    print(f"bills: {len(todo)} to fetch ({len(bills)-len(todo)} already parsed)")

    prog, failures = Progress(len(todo), "bills"), []
    for b in todo:
        res = fetcher.fetch(b["url"], force=args.refresh)
        store.log_fetch(res)
        if not res.ok:
            failures.append((b["num"], res.status))
            prog.tick(f"FAIL {b['label']} HTTP {res.status}")
            continue
        try:
            rec = flsenate.parse_bill_page(res.text(), args.session, b["num"])
            store.save_bill(rec)
        except Exception as exc:                       # keep going, report at end
            failures.append((b["num"], f"parse: {exc}"))
        if prog.n % 50 == 0:
            store.commit()
        prog.tick()

    store.commit()
    print(f"\nbills: done. failures={len(failures)}")
    for num, why in failures[:25]:
        print(f"  {num}: {why}")
    return 1 if failures else 0


def cmd_bill(args) -> int:
    """Pull or refresh specific bills on demand, without touching the rest."""
    fetcher, store, _ = _paths(args.session)
    nums, failures = [], []
    for tok in args.numbers:                      # accepts 797 or "HB 797"
        m = re.search(r"(\d+)", tok)
        if m:
            nums.append(int(m.group(1)))
        else:
            print(f"  skipping unparseable bill id {tok!r}")

    for num in nums:
        url = f"https://www.flsenate.gov/Session/Bill/{args.session}/{num}"
        res = fetcher.fetch(url, force=not args.cached)
        store.log_fetch(res)
        if not res.ok:
            failures.append((num, f"HTTP {res.status}"))
            print(f"  {num}: HTTP {res.status}")
            continue
        rec = flsenate.parse_bill_page(res.text(), args.session, num)
        store.save_bill(rec)
        state = "DEAD" if is_dead(rec.get("last_action", "")) else "live"
        print(f"  {rec['label']:14} [{state}] {rec['title'][:52]}")
        print(f"    {len(rec['history'])} events, {len(rec['texts'])} text rows, "
              f"{len(rec['analyses'])} analyses, {len(rec['amendments'])} amendments")

        if args.with_docs:
            urls = [t["url"] for t in rec["texts"]
                    if t["fmt"] == "HTML" or not any(
                        o["version"] == t["version"] and o["fmt"] == "HTML"
                        for o in rec["texts"])]
            urls += [a["url"] for a in rec["analyses"]]
            for u in urls:
                d = fetcher.fetch(u, force=not args.cached)
                store.log_fetch(d)
                if not d.ok:
                    failures.append((num, f"doc HTTP {d.status}"))
            print(f"    fetched {len(urls)} documents")

    store.commit()
    if failures:
        print(f"\nfailures: {len(failures)}")
    return 1 if failures else 0


def cmd_docs(args) -> int:
    fetcher, store, _ = _paths(args.session)
    db = store.db
    jobs = []

    if "text" in args.kinds:
        # Senate text is HTML (with Insert/Remove markup); House text is
        # PDF-only. Take HTML where it exists and fall back to PDF, so every
        # version is captured exactly once.
        q = """SELECT url FROM bill_text t WHERE session=? AND (
                   fmt='HTML' OR NOT EXISTS (
                       SELECT 1 FROM bill_text h
                       WHERE h.session=t.session AND h.num=t.num
                         AND h.version=t.version AND h.fmt='HTML'))"""
        if args.text_pdf:
            q = "SELECT url FROM bill_text WHERE session=?"
        jobs += [("text", r["url"]) for r in db.execute(q, (args.session,))]
    if "analyses" in args.kinds:
        jobs += [("analysis", r["url"]) for r in
                 db.execute("SELECT url FROM analysis WHERE session=?", (args.session,))]
    if "votes" in args.kinds:
        jobs += [("vote", r["url"]) for r in
                 db.execute("SELECT url FROM vote WHERE session=? AND url<>''",
                            (args.session,))]
    if "amendments" in args.kinds:
        jobs += [("amendment", r["url"]) for r in
                 db.execute("SELECT url FROM amendment WHERE session=?", (args.session,))]

    seen, uniq = set(), []
    for kind, url in jobs:
        if url and url not in seen:
            seen.add(url)
            uniq.append((kind, url))

    if args.order in ORDER_SQL:
        rank = {r["num"]: i for i, r in
                enumerate(db.execute(ORDER_SQL[args.order], (args.session,)))}
        keyof = lambda u: rank.get(
            int(re.search(r"/Bill/[^/]+/(\d+)/", u).group(1))
            if re.search(r"/Bill/[^/]+/(\d+)/", u) else -1, 1 << 30)
        uniq.sort(key=lambda kv: keyof(kv[1]))

    if args.limit:
        uniq = uniq[:args.limit]

    print(f"docs: {len(uniq)} documents queued ({', '.join(sorted(args.kinds))})")
    prog, failures, cached = Progress(len(uniq), "docs"), [], 0
    for kind, url in uniq:
        res = fetcher.fetch(url, force=args.refresh)
        store.log_fetch(res)
        if res.from_cache:
            cached += 1
        elif not res.ok:
            failures.append((kind, url, res.status))
        if prog.n % 100 == 0:
            store.commit()
        prog.tick()

    store.commit()
    print(f"\ndocs: done. cached={cached} failures={len(failures)}")
    for kind, url, st in failures[:25]:
        print(f"  {kind} HTTP {st}: {url}")
    return 1 if failures else 0


def cmd_diff(args) -> int:
    """Show what a bill adds and deletes -- no model involved."""
    from . import diff as diffmod

    fetcher, store, _ = _paths(args.session)
    db = store.db
    num = int(re.search(r"(\d+)", args.number).group(1))

    bill = db.execute("SELECT * FROM bill WHERE session=? AND num=?",
                      (args.session, num)).fetchone()
    if not bill:
        print(f"bill {num} not ingested; run: flba --session {args.session} "
              f"bill {num}", file=sys.stderr)
        return 2

    rows = db.execute(
        "SELECT version, fmt, url FROM bill_text WHERE session=? AND num=?",
        (args.session, num)).fetchall()
    versions = {}
    for r in rows:                       # HTML wins where both exist
        cur = versions.get(r["version"])
        if cur is None or (cur["fmt"] == "PDF" and r["fmt"] == "HTML"):
            versions[r["version"]] = r
    if not versions:
        print(f"no text for {bill['label']}", file=sys.stderr)
        return 2

    key = args.version or list(versions)[-1 if args.latest else 0]
    if key not in versions:
        print(f"no such version {key!r}; have: {', '.join(versions)}",
              file=sys.stderr)
        return 2
    chosen = versions[key]

    res = fetcher.fetch(chosen["url"])
    store.log_fetch(res)
    if not res.ok:
        print(f"fetch failed: HTTP {res.status}", file=sys.stderr)
        return 1

    if chosen["fmt"] == "HTML":
        d = diffmod.parse_senate_html(res.text())
    else:
        d = diffmod.parse_house_pdf(res.path)

    st = d.stats()
    print(f"{bill['label']} — {bill['title']}")
    print(f"  version {chosen['version']} ({chosen['fmt']}, via {st['source']})")
    print(f"  {st['words_inserted']} words added in {st['insertions']} runs; "
          f"{st['words_deleted']} words deleted in {st['deletions']} runs")

    if args.json:
        print(json.dumps({"bill": bill["label"], "version": chosen["version"],
                          "stats": st,
                          "segments": [{"kind": x.kind, "text": x.text,
                                        "line": x.line, "page": x.page}
                                       for x in d.segments
                                       if x.kind != diffmod.PLAIN]}, indent=1))
        return 0

    for label, segs in (("ADDS", d.insertions), ("DELETES", d.deletions)):
        print(f"\n  {label}")
        for seg in segs[:args.show]:
            loc = f"line {seg.line}" if seg.line else "—"
            print(f"    {loc:>10}  {seg.text[:96]}")
        if len(segs) > args.show:
            print(f"    ... and {len(segs) - args.show} more "
                  f"(raise with --show, or use --json)")
    return 0


def cmd_track(args) -> int:
    """Show where a bill sits in the process -- deterministic, no model."""
    from .stages import kind_of, pathway, track

    _, store, _ = _paths(args.session)
    db = store.db

    if args.summary:
        from collections import Counter
        rows = db.execute("SELECT num,label,chapter_law FROM bill WHERE session=?",
                          (args.session,)).fetchall()
        hist: dict[int, list] = {}
        for r in db.execute("SELECT num,date,chamber,action FROM history"
                            " WHERE session=? ORDER BY num,seq", (args.session,)):
            hist.setdefault(r["num"], []).append(dict(r))
        outcomes, stages = Counter(), Counter()
        for b in rows:
            p = track(hist.get(b["num"], []), b["chapter_law"], kind_of(b["label"]))
            outcomes[p.outcome_label] += 1
            stages[p.stage_label] += 1
        print(f"session {args.session} — {len(rows)} bills\n")
        print("  outcome")
        for k, v in outcomes.most_common():
            print(f"    {v:5}  {100*v/len(rows):4.1f}%  {k}")
        print("\n  furthest stage reached")
        for k, v in stages.most_common():
            print(f"    {v:5}  {100*v/len(rows):4.1f}%  {k}")
        return 0

    if not args.number:
        print("give a bill number, or --summary", file=sys.stderr)
        return 2

    num = int(re.search(r"(\d+)", args.number).group(1))
    bill = db.execute("SELECT * FROM bill WHERE session=? AND num=?",
                      (args.session, num)).fetchone()
    if not bill:
        print(f"bill {num} not ingested; run: flba --session {args.session} "
              f"bill {num}", file=sys.stderr)
        return 2

    events = [dict(r) for r in db.execute(
        "SELECT date,chamber,action FROM history WHERE session=? AND num=?"
        " ORDER BY seq", (args.session, num))]
    prog = track(events, bill["chapter_law"], kind_of(bill["label"]))

    if args.json:
        print(json.dumps({"bill": bill["label"], "title": bill["title"],
                          **prog.as_dict(), "pathway": pathway(prog)}, indent=1))
        return 0

    print(f"{bill['label']} — {bill['title']}")
    print(f"  {prog.kind_label} · {prog.outcome_label} · {prog.percent}% of the way")
    if prog.chapter_law:
        print(f"  chapter law {prog.chapter_law}")
    if prog.companion:
        print(f"  companion that carried it: {prog.companion}")
    if prog.died_in:
        print(f"  died in: {prog.died_in}")
    print()
    for step in pathway(prog):
        mark = "*" if step["current"] else ("x" if step["reached"] else " ")
        date = step["date"] or ""
        print(f"  [{mark}] {step['label']:<36} {date}")
    return 0


def cmd_crossref(args) -> int:
    """Build the statute cross-reference index from cached bill text."""
    from .diff import parse_house_pdf, parse_senate_html
    from .statutes import cross_reference

    fetcher, store, _ = _paths(args.session)
    db = store.db
    rows = db.execute(
        "SELECT num, MAX(rowid) mr FROM bill_text WHERE session=? GROUP BY num",
        (args.session,)).fetchall()

    jobs = []
    for r in rows:
        t = db.execute("SELECT version,fmt,url FROM bill_text WHERE rowid=?",
                       (r["mr"],)).fetchone()
        # prefer HTML for this version if both formats exist
        h = db.execute("SELECT url FROM bill_text WHERE session=? AND num=?"
                       " AND version=? AND fmt='HTML'",
                       (args.session, r["num"], t["version"])).fetchone()
        url, fmt = (h["url"], "HTML") if h else (t["url"], t["fmt"])
        if fetcher.local_path(url).exists():
            jobs.append((r["num"], t["version"], fmt, url))
    if args.limit:
        jobs = jobs[:args.limit]

    print(f"crossref: {len(jobs)} bills with cached text "
          f"({len(rows) - len(jobs)} not yet fetched)")
    prog, total, failed = Progress(len(jobs), "crossref"), 0, 0
    for num, version, fmt, url in jobs:
        path = fetcher.local_path(url)
        try:
            if fmt == "HTML":
                d = parse_senate_html(path.read_text(encoding="utf-8",
                                                     errors="replace"))
            else:
                d = parse_house_pdf(path)
            refs = cross_reference(d)
            store.save_statute_refs(args.session, num, version, refs)
            store.save_render(args.session, num, version, fmt, d.segments)
            total += len(refs)
        except Exception as exc:
            failed += 1
            if failed <= 5:
                print(f"  {num}: {exc}")
        prog.tick()
    print(f"\ncrossref: {total} statute references indexed, {failed} failures")
    return 0


def cmd_statutes(args) -> int:
    """A bill's statute references, or every bill touching one statute."""
    _, store, _ = _paths(args.session)
    db = store.db

    if args.statute:
        cite = args.statute.lstrip("s. ").strip()
        rows = db.execute(
            "SELECT r.num, b.label, b.title, r.action, r.bill_section,"
            "       r.words_added, r.words_deleted, b.chapter_law"
            "  FROM statute_ref r JOIN bill b"
            "    ON b.session=r.session AND b.num=r.num"
            " WHERE r.session=? AND r.statute=?"
            " ORDER BY b.chapter_law IS NULL, r.num", (args.session, cite)
        ).fetchall()
        if not rows:
            print(f"no bill in {args.session} touches s. {cite}"
                  f" (run `crossref` first?)")
            return 1
        print(f"s. {cite} — {len(rows)} bill(s) in session {args.session}")
        print(f"  https://www.flsenate.gov/laws/statutes/2025/{cite}\n")
        for r in rows:
            law = f"  [Ch. {r['chapter_law']}]" if r["chapter_law"] else ""
            print(f"  {r['label']:<16} {r['action']:<8} "
                  f"+{r['words_added']}/-{r['words_deleted']}{law}")
            print(f"      {r['title'][:82]}")
        return 0

    if not args.number:
        print("give a bill number, or --statute <cite>", file=sys.stderr)
        return 2
    num = int(re.search(r"(\d+)", args.number).group(1))
    bill = db.execute("SELECT label,title FROM bill WHERE session=? AND num=?",
                      (args.session, num)).fetchone()
    rows = db.execute(
        "SELECT * FROM statute_ref WHERE session=? AND num=?"
        " ORDER BY bill_section IS NULL, bill_section, statute",
        (args.session, num)).fetchall()
    if not bill:
        print(f"bill {num} not ingested", file=sys.stderr)
        return 2
    print(f"{bill['label']} — {bill['title']}")
    if not rows:
        have = db.execute("SELECT COUNT(*) c FROM bill_text WHERE session=?"
                          " AND num=?", (args.session, num)).fetchone()["c"]
        indexed = db.execute("SELECT COUNT(*) c FROM statute_ref WHERE session=?",
                             (args.session,)).fetchone()["c"]
        if not have:
            print("  this bill has no text on file at all")
        elif not indexed:
            print(f"  nothing indexed yet — run:"
                  f" flba --session {args.session} crossref")
        else:
            print(f"  text not cached yet — run:"
                  f" flba --session {args.session} bill {num} --with-docs,"
                  f" then crossref")
        return 1
    print(f"  {len(rows)} statute reference(s)\n")
    if args.json:
        print(json.dumps([dict(r) for r in rows], indent=1))
        return 0
    for r in rows:
        where = f"line {r['line_start']}" if r["line_start"] else "title only"
        churn = (f"+{r['words_added']}/-{r['words_deleted']}"
                 if (r["words_added"] or r["words_deleted"]) else "")
        sec = f"§{r['bill_section']}" if r["bill_section"] else "  "
        print(f"  {sec:>4}  {r['action']:<8} s. {r['statute']:<12} "
              f"{where:<12} {churn}")
        if r["scope"]:
            print(f"        {r['scope'][:88]}")
    return 0


def cmd_build(args) -> int:
    """Render the static site the public server will serve."""
    from .site import build

    out = Path(args.out) if args.out else ROOT / "site"
    print(f"building session {args.session} -> {out}")
    stats = build(DATA / "index.sqlite", out, args.session, limit=args.limit)
    print(f"\n  {stats['bills']} bill pages")
    print(f"  {stats['statutes']} statute pages")
    print(f"  {stats['files']} files, {stats['bytes']/1e6:.1f} MB total")
    print(f"\n  open: {out / 'index.html'}")
    return 0


def cmd_analyze(args) -> int:
    """Run the model over bills and store what survives verification."""
    from .analysis.analyze import analyze
    from .analysis.backend import Backend

    _, store, _ = _paths(args.session)
    db = store.db
    backend = Backend(base_url=args.base_url, model=args.model,
                      api_key=args.api_key)

    if args.show:
        num = int(re.search(r"(\d+)", args.show).group(1))
        rec = store.load_analysis(args.session, num)
        if not rec:
            print(f"no analysis stored for bill {num}", file=sys.stderr)
            return 2
        bill = db.execute("SELECT label,title FROM bill WHERE session=? AND num=?",
                          (args.session, num)).fetchone()
        print(f"{bill['label']} — {bill['title']}")
        print(f"  model {rec['model']}, generated {rec['generated_at']}\n")
        print(f"  {rec['one_line']}\n")
        print(f"  {rec['summary']}\n")
        if rec["who_is_affected"]:
            print("  AFFECTS: " + "; ".join(rec["who_is_affected"]) + "\n")
        if rec["provisions"]:
            print("  PROVISIONS")
            for p in rec["provisions"]:
                print(f"    [{p['significance']}] {p['heading']}"
                      f"{' — s. ' + p['statute'] if p.get('statute') else ''}")
                print(f"       {p['effect']}")
                print(f"       “{p['quote'][:110]}” [{p['quote_status']}]")
        if rec["implications"]:
            print("\n  WHAT THE LANGUAGE PERMITS")
            for i in rec["implications"]:
                print(f"    [{i['certainty']}] {i['consequence']}")
                print(f"       “{i['quote'][:110]}”")
        if rec["unclear"]:
            print("\n  LEFT UNCLEAR")
            for u in rec["unclear"]:
                print(f"    - {u}")
        print(f"\n  stats: {rec['stats']}")
        return 0

    if not backend.available():
        print(f"no model server at {args.base_url}\n"
              f"start one with:\n"
              f"  python -m mlx_vlm.server --model {args.model} --port 8080",
              file=sys.stderr)
        return 2

    if args.number:
        nums = [int(re.search(r"(\d+)", args.number).group(1))]
    else:
        done = set() if args.refresh else store.analysed_nums(args.session)
        rows = db.execute(ORDER_SQL[args.order] if args.order in ORDER_SQL
                          else ORDER_SQL["number"], (args.session,)).fetchall()
        nums = [r["num"] for r in rows if r["num"] not in done]
        if args.limit:
            nums = nums[:args.limit]

    print(f"analyze: {len(nums)} bill(s) via {args.model}")
    prog, failed = Progress(len(nums), "analyze", every=5), 0
    for num in nums:
        try:
            result = analyze(db, args.session, num, backend,
                             log=(print if len(nums) == 1 else lambda *_: None))
        except Exception as exc:
            failed += 1
            print(f"  {num}: {exc}")
            prog.tick()
            continue
        if result is None:
            failed += 1
            prog.tick()
            continue
        store.save_analysis(result)
        if len(nums) == 1:
            print(f"\n  {result.summary.get('one_line','')}")
            print(f"  stored. view with: flba --session {args.session} "
                  f"analyze --show {num}")
        prog.tick(f"{result.label} "
                  f"({result.stats['claims_kept']} claims, "
                  f"{result.stats['claims_dropped']} dropped)")

    s = backend.stats
    print(f"\nanalyze: done. failed={failed}")
    print(f"  {s['calls']} calls, {s['cache_hits']} cache hits, "
          f"{s['completion_tokens']:,} tokens generated, "
          f"{s['seconds']/60:.1f} min")
    return 1 if failed else 0


def cmd_status(args) -> int:
    _, store, index_path = _paths(args.session)
    db, s = store.db, args.session
    one = lambda q: db.execute(q, (s,)).fetchone()[0]

    print(f"session {s}")
    if index_path.exists():
        idx = json.loads(index_path.read_text())
        print(f"  enumerated      : {idx['scraped_total']} "
              f"(site reported {idx['reported_total']})")
    print(f"  bills parsed    : {one('SELECT COUNT(*) FROM bill WHERE session=?')}")
    for tbl in ("history", "committee_ref", "bill_text", "analysis",
                "vote", "amendment", "citation", "related"):
        print(f"  {tbl:15} : {one(f'SELECT COUNT(*) FROM {tbl} WHERE session=?')}")
    print(f"  fetched ok      : "
          f"{db.execute('SELECT COUNT(*) FROM fetch WHERE status=200').fetchone()[0]}")
    print(f"  fetch failures  : "
          f"{db.execute('SELECT COUNT(*) FROM fetch WHERE status<>200').fetchone()[0]}")

    raw = DATA / "raw"
    if raw.exists():
        n = sum(1 for _ in raw.rglob("*") if _.is_file())
        mb = sum(f.stat().st_size for f in raw.rglob("*") if f.is_file()) / 1e6
        print(f"  raw cache       : {n} files, {mb:.1f} MB")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="flba")
    p.add_argument("--session", default="2026",
                   help="session year, e.g. 2026 or 2026E")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, fn in (("enumerate", cmd_enumerate), ("bills", cmd_bills),
                     ("bill", cmd_bill), ("docs", cmd_docs),
                     ("diff", cmd_diff), ("track", cmd_track),
                     ("crossref", cmd_crossref), ("statutes", cmd_statutes),
                     ("analyze", cmd_analyze), ("build", cmd_build),
                     ("status", cmd_status)):
        sp = sub.add_parser(name)
        sp.set_defaults(func=fn)
        sp.add_argument("--refresh", action="store_true",
                        help="re-fetch even if cached")
        if name == "bill":
            sp.add_argument("numbers", nargs="+",
                            help="bill numbers, e.g. 797 or 'HB 797'")
            sp.add_argument("--with-docs", action="store_true",
                            help="also fetch this bill's text and analyses")
            sp.add_argument("--cached", action="store_true",
                            help="use the cache instead of re-fetching")
        if name == "diff":
            sp.add_argument("number", help="bill number, e.g. 1277")
            sp.add_argument("--version", help="which text version")
            sp.add_argument("--latest", action="store_true",
                            help="use the last version rather than the first")
            sp.add_argument("--show", type=int, default=12,
                            help="how many runs to print per side")
            sp.add_argument("--json", action="store_true")
        if name == "track":
            sp.add_argument("number", nargs="?", help="bill number, e.g. 1452")
            sp.add_argument("--summary", action="store_true",
                            help="outcome and stage distribution for the session")
            sp.add_argument("--json", action="store_true")
        if name == "statutes":
            sp.add_argument("number", nargs="?", help="bill number")
            sp.add_argument("--statute", help="reverse lookup, e.g. 286.011")
            sp.add_argument("--json", action="store_true")
        if name == "crossref":
            sp.add_argument("--limit", type=int, default=0)
        if name == "analyze":
            sp.add_argument("number", nargs="?", help="one bill number")
            sp.add_argument("--show", help="print the stored analysis for a bill")
            sp.add_argument("--limit", type=int, default=0,
                            help="stop after N bills -- run in chunks")
            sp.add_argument("--order", default="activity",
                            choices=["number", "live", "activity"])
            sp.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
            sp.add_argument("--model",
                            default="mlx-community/Qwen3.8-27B-4bit")
            sp.add_argument("--api-key", default=None)
        if name == "build":
            sp.add_argument("--out", help="output directory (default ./site)")
            sp.add_argument("--limit", type=int, default=0)
        if name in ("bills", "docs"):
            sp.add_argument("--limit", type=int, default=0,
                            help="stop after N items -- use this to run the "
                                 "backfill in short chunks")
            sp.add_argument("--order", default="number",
                            choices=["number", "live", "activity"],
                            help="live: undead bills first; "
                                 "activity: most-worked bills first")
        if name == "docs":
            sp.add_argument("--kinds", default="text,analyses",
                            type=lambda v: set(v.split(",")))
            sp.add_argument("--text-pdf", action="store_true",
                            help="also fetch PDF bill text (HTML only by default)")

    args = p.parse_args(argv)
    return args.func(args)
