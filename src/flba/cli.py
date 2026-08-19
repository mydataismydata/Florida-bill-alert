"""Command line interface for the Florida Bill Alert ingest pipeline.

    python -m flba enumerate --session 2026
    python -m flba bills     --session 2026
    python -m flba docs      --session 2026
    python -m flba status    --session 2026
"""
from __future__ import annotations

import argparse
import json
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


def cmd_docs(args) -> int:
    fetcher, store, _ = _paths(args.session)
    db = store.db
    jobs = []

    if "text" in args.kinds:
        q = ("SELECT url FROM bill_text WHERE session=? AND fmt='HTML'"
             if not args.text_pdf else
             "SELECT url FROM bill_text WHERE session=?")
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
                     ("docs", cmd_docs), ("status", cmd_status)):
        sp = sub.add_parser(name)
        sp.set_defaults(func=fn)
        sp.add_argument("--refresh", action="store_true",
                        help="re-fetch even if cached")
        if name in ("bills", "docs"):
            sp.add_argument("--limit", type=int, default=0)
        if name == "docs":
            sp.add_argument("--kinds", default="text,analyses",
                            type=lambda v: set(v.split(",")))
            sp.add_argument("--text-pdf", action="store_true",
                            help="also fetch PDF bill text (HTML only by default)")

    args = p.parse_args(argv)
    return args.func(args)
