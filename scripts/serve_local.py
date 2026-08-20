#!/usr/bin/env python3
"""Serve the built site locally, with the operator controls wired up.

This is the analysis box, not the public host. It exists so the re-run button
on a `--local` build has something to post to: it runs the model, rebuilds the
affected page, and sends you back to it.

Nothing here ever ships. The public server is a file host -- no model, no
pipeline, no database -- and scripts/deploy.sh refuses to push a site built
with --local. That is what keeps the gap structural rather than configured.

    python scripts/serve_local.py                 # http://127.0.0.1:8791
    python scripts/serve_local.py --port 9000

Bound to the loopback interface, deliberately: it runs a subprocess on
request, so it must not be reachable from the network.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sqlite3
import sys
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
DB = ROOT / "data" / "index.sqlite"
# The bill number is the only thing a request controls, and it reaches a
# subprocess, so it is matched rather than escaped.
RE_ANALYZE = re.compile(r"^/_reanalyze/(\d{1,6})$")


def known_bill(session: str, num: int) -> bool:
    if not DB.exists():
        return False
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as db:
        row = db.execute("SELECT 1 FROM bill WHERE session=? AND num=?",
                         (session, num)).fetchone()
    return row is not None


def run(args: list[str], timeout: int) -> tuple[bool, str]:
    """No shell, fixed argv, output captured for the browser."""
    try:
        p = subprocess.run([sys.executable, "-m", "flba", *args],
                           cwd=ROOT, timeout=timeout, capture_output=True,
                           text=True, env={**_env()})
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()
        return False, tail[-1] if tail else f"exit {p.returncode}"
    return True, p.stdout


def _env() -> dict:
    import os
    env = dict(os.environ)
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


class Handler(SimpleHTTPRequestHandler):
    session = "2026"
    model: str | None = None

    def do_POST(self):
        m = RE_ANALYZE.match(self.path)
        if not m:
            return self.send_error(404, "no such endpoint")
        num = int(m.group(1))
        if not known_bill(self.session, num):
            return self._reply(404, {"error": f"bill {num} is not in the index"})

        t0 = time.time()
        extra = ["--model", self.model] if self.model else []
        ok, detail = run(["--session", self.session, "analyze", str(num), *extra],
                         timeout=1800)
        if ok:
            # Only this bill's pages, so the button stays quick. The index
            # tiles keep their previous counts until the next full build.
            #
            # --local is not optional here: without it the rebuild strips the
            # button from the very page that was just clicked, and clears the
            # marker deploy.sh checks. This server only ever serves a local
            # build, so the flag always applies.
            ok, detail = run(["--session", self.session, "build",
                              "--only", str(num), "--local"], timeout=600)
        seconds = round(time.time() - t0, 1)
        self.log_message("re-analyze %d: %s (%.1fs)", num,
                         "ok" if ok else detail.replace("%", "%%"), seconds)

        if not ok:
            return self._reply(500, {"error": detail, "seconds": seconds})
        if "application/json" in (self.headers.get("Accept") or ""):
            return self._reply(200, {"ok": True, "num": num, "seconds": seconds})
        self.send_response(303)
        self.send_header("Location", f"/bills/{num}.html")
        self.end_headers()

    def _reply(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # A stale bill page after a re-run is the one thing this server must
        # not serve.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--session", default="2026")
    ap.add_argument("--model", help="override the model the button runs")
    a = ap.parse_args()

    if not (SITE / "index.html").exists():
        print("no built site -- run: flba --session 2026 build --local", file=sys.stderr)
        return 2
    marker = SITE / ".local-build"
    if not marker.exists():
        print("site was not built with --local, so the re-run buttons are absent.\n"
              "  rebuild with: flba --session 2026 build --local", file=sys.stderr)

    Handler.session, Handler.model = a.session, a.model
    handler = partial(Handler, directory=str(SITE))
    with ThreadingHTTPServer(("127.0.0.1", a.port), handler) as httpd:
        print(f"serving {SITE} at http://127.0.0.1:{a.port}  (loopback only)")
        print("re-run buttons post to /_reanalyze/<num>.  ctrl-c to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
