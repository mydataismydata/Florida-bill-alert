#!/usr/bin/env python3
"""Serve the built site locally, with the operator controls wired up.

This is the analysis box, not the public host. It exists so the operator
controls on a `--local` build have something to post to. "Re-run the
analysis" runs the model, rebuilds the affected page, and sends you back to
it. "Edit the text" opens a form over the stored headline, summary and
provisions, and saving it rebuilds the page the same way (see flba/admin.py).

Nothing here ever ships. The public server is a file host -- no model, no
pipeline, no database -- and scripts/deploy.sh refuses to push a site built
with --local. That is what keeps the gap structural rather than configured.

    python scripts/serve_local.py                 # http://127.0.0.1:8791
    python scripts/serve_local.py --port 9000

Bound to the loopback interface, deliberately: it runs a subprocess on
request, so it must not be reachable from the network. From another machine,
reach it through SSH:

    ssh -N -L 8791:127.0.0.1:8791 Seans-Mac-Studio.local

A page on any website can still make the browser post to 127.0.0.1, so the
edit form carries a token minted when this server starts, and a save without
it is refused.
"""
from __future__ import annotations

import argparse
import hmac
import json
import re
import secrets
import subprocess
import sqlite3
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
DB = ROOT / "data" / "index.sqlite"
sys.path.insert(0, str(ROOT / "src"))

from flba import admin            # noqa: E402
from flba.site import bill_dir    # noqa: E402
from flba.store import Store      # noqa: E402

# The session and bill number are the only things a request controls, and
# they reach a subprocess, so they are matched rather than escaped. Bill
# numbers start again each session, so the session is part of the address.
RE_ANALYZE = re.compile(r"^/_reanalyze/(\d{4}[A-Z]?)/(\d{1,6})$")
RE_EDIT = re.compile(r"^/_edit/(\d{4}[A-Z]?)/(\d{1,6})$")

# Minted per run. The edit form carries it, and a save without it is refused.
TOKEN = secrets.token_urlsafe(24)
MAX_FORM = 200_000

# A re-run is a job, not a request. It takes minutes, and whoever started it
# should be free to read another bill meanwhile, so the POST only queues the
# job and answers at once. The job runs on in this process whether or not
# anyone is still watching: it stores the analysis and rebuilds the page.
# The bill page asks GET /_reanalyze/<session>/<num> how the job is doing.
#
# GLM takes one bill at a time, so jobs started together wait their turn.
# Jobs live in memory: restarting this server forgets them, and a job still
# running when it stops is lost with it.
MODEL_LOCK = threading.Lock()
JOBS: dict[int, dict] = {}
JOBS_LOCK = threading.Lock()
# A re-run and an edit both rewrite one bill's row and page, so they take
# turns per bill. Different bills do not wait on each other.
_BILL_LOCKS: dict[int, threading.Lock] = {}


def bill_lock(num: int) -> threading.Lock:
    with JOBS_LOCK:
        return _BILL_LOCKS.setdefault(num, threading.Lock())


def job_state(num: int) -> dict:
    with JOBS_LOCK:
        return dict(JOBS.get(num) or {"state": "idle"})


def busy(num: int) -> bool:
    return job_state(num)["state"] in ("queued", "running")


def _set(num: int, **fields) -> None:
    with JOBS_LOCK:
        JOBS.setdefault(num, {}).update(fields)


def _now() -> str:
    return time.strftime("%H:%M")


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S ") + message, file=sys.stderr,
          flush=True)


def start_job(num: int, session: str, model: str | None) -> dict:
    """Queue a re-run of one bill, unless one is already queued or running."""
    with JOBS_LOCK:
        job = JOBS.get(num)
        if job and job["state"] in ("queued", "running"):
            return dict(job)
        JOBS[num] = {"state": "queued", "queued": _now(), "started": "",
                     "finished": "", "error": "", "seconds": None}
    threading.Thread(target=_run_job, args=(num, session, model),
                     name=f"rerun-{num}", daemon=True).start()
    log(f"re-analyze {num}: queued")
    return job_state(num)


def _run_job(num: int, session: str, model: str | None) -> None:
    with MODEL_LOCK:
        _set(num, state="running", started=_now())
        t0 = time.time()
        extra = ["--model", model] if model else []
        try:
            with bill_lock(num):
                ok, detail = run(["--session", session, "analyze", str(num),
                                  *extra], timeout=1800)
                if ok:
                    ok, detail = rebuild(session, num)
        except Exception as exc:       # a job must never stay "running"
            ok, detail = False, f"{type(exc).__name__}: {exc}"
    seconds = round(time.time() - t0, 1)
    _set(num, state="done" if ok else "failed", finished=_now(),
         seconds=seconds, error="" if ok else detail)
    log(f"re-analyze {num}: {'ok' if ok else detail} ({seconds}s)")


def rebuild(session: str, num: int) -> tuple[bool, str]:
    # Only this bill's pages, so the controls stay quick. The index tiles
    # keep their previous counts until the next full build.
    #
    # --local is not optional here: without it the rebuild strips the
    # controls from the very page that was just used, and clears the marker
    # deploy.sh checks. This server only ever serves a local build, so the
    # flag always applies.
    return run(["--session", session, "build", "--only", str(num), "--local"],
               timeout=600)


_BUSY = ("A re-run of this bill is in progress. Its result would replace "
         "anything saved now, so wait until it finishes, then edit.")


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

    def _ours(self, m) -> int | None:
        """The bill number, if the request is for this server's session.

        One server runs one session, and its jobs and locks are keyed by
        number alone. A button on another session's page is refused rather
        than run against this session's bill of the same number."""
        if m.group(1) != self.session:
            self.send_error(409, f"this server runs session {self.session}; "
                                 f"start it with --session {m.group(1)}")
            return None
        return int(m.group(2))

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        m = RE_ANALYZE.match(path)
        if m:
            num = self._ours(m)
            return None if num is None else self._reply(200, job_state(num))
        m = RE_EDIT.match(path)
        if not m:
            return super().do_GET()
        num = self._ours(m)
        if num is None:
            return None
        store = Store(DB)
        try:
            bill, rec = admin.load(store.db, self.session, num)
        finally:
            store.db.close()
        if bill is None or rec is None:
            return self.send_error(404, f"no analysis stored for bill {num}")
        self._page(200, admin.form_html(self.session, num, bill,
                                        admin.values_from_record(rec), TOKEN,
                                        _BUSY if busy(num) else ""))

    def do_POST(self):
        m = RE_EDIT.match(self.path)
        if m:
            num = self._ours(m)
            return None if num is None else self._edit(num)
        m = RE_ANALYZE.match(self.path)
        if not m:
            return self.send_error(404, "no such endpoint")
        num = self._ours(m)
        if num is None:
            return None
        if not known_bill(self.session, num):
            return self._reply(404, {"error": f"bill {num} is not in the index"})
        job = start_job(num, self.session, self.model)
        if "application/json" in (self.headers.get("Accept") or ""):
            return self._reply(202, job)
        # Without JavaScript: back to the bill, which shows the job running.
        self.send_response(303)
        self.send_header("Location", f"/{bill_dir(self.session)}/{num}.html")
        self.end_headers()

    def _edit(self, num: int):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_FORM:
            return self.send_error(413, "form too large")
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"),
                        keep_blank_values=True)
        if not hmac.compare_digest((form.get("token") or [""])[0], TOKEN):
            return self.send_error(403, "stale or missing form token -- "
                                        "reload the edit page")
        with bill_lock(num):
            store = Store(DB)
            try:
                bill, rec = admin.load(store.db, self.session, num)
                if bill is None or rec is None:
                    return self.send_error(404, f"no analysis stored for bill {num}")
                values = admin.values_from_form(rec, form)
                error = _BUSY if busy(num) else admin.problems(values, rec)
                if error:
                    return self._page(400, admin.form_html(
                        self.session, num, bill, values, TOKEN, error))
                changed = admin.save(store.db, self.session, num, values, rec)
            finally:
                store.db.close()
            ok, detail = rebuild(self.session, num) if changed else (True, "")
        log(f"edit {num}: {changed} change(s), {'ok' if ok else detail}")
        if not ok:
            return self._page(500, f"<p>Saved, but the page rebuild failed: "
                                   f"{admin.escape(detail)}</p>")
        self.send_response(303)
        self.send_header("Location", f"/{bill_dir(self.session)}/{num}.html")
        self.end_headers()

    def _page(self, code: int, html: str):
        body = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
        print(f"serving {SITE} at http://127.0.0.1:{a.port}  (loopback only)",
              flush=True)
        print("re-run buttons post to /_reanalyze/<session>/<num>, edits to "
              "/_edit/<session>/<num>.  ctrl-c to stop.", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
