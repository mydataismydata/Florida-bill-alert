"""Analysis requests from the public site: read, worked, answered.

The "Request AI analysis" button on a bill page writes a row on the public
server and stops there. Nothing on that server can reach this machine, so
this machine goes and asks: over the SSH connection the deploy already uses,
it runs requests.php, which prints the bills people asked for and when. Who
asked does not come with it -- the addresses stay on the server.

Three parts, all in the nightly run:

  fetch    the open requests, one entry per bill
  analyse  the requested bills, ahead of the night's own work
  answer   once the pages are up, a payload naming the requested bills whose
           published page now carries an analysis. send.php writes to
           whoever asked and closes their requests.

"Answered" is read off the published page rather than the database: a bill
is ready when the page that went up no longer offers the button. That is
exactly what the person who asked will see when they follow the link.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .site import bill_dir

# How the public host runs PHP. The site's PHP lives in a container there;
# the cron that runs send.php goes through the same one.
DEFAULT_PHP = "docker exec -u 33 sessionwatch-php php"

SESSION_RE = re.compile(r"^\d{4}[A-Z]?$")

# The bill page's request form posts here, and a page carries it only while
# the bill has no analysis (templates/bill.html).
FORM_MARK = "request-analysis.php"


class Unavailable(RuntimeError):
    """The public host could not be asked."""


def fetch(host: str | None = None, path: str | None = None,
          php: str | None = None, timeout: int = 60) -> list[dict]:
    """The open requests on the public host, oldest first."""
    host = host or os.environ.get("FLBA_HOST", "")
    path = (path or os.environ.get("FLBA_PATH", "")).rstrip("/")
    php = php or os.environ.get("FLBA_PHP", DEFAULT_PHP)
    if not host or not path:
        raise Unavailable("FLBA_HOST and FLBA_PATH are not set (see .env)")
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", host,
           f"{php} {shlex.quote(path + '/requests.php')}"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unavailable(f"ssh {host}: {exc}") from exc
    if out.returncode != 0:
        raise Unavailable(f"ssh {host} exited {out.returncode}: "
                          f"{(out.stderr or out.stdout).strip()[:300]}")
    return parse(out.stdout)


def parse(text: str) -> list[dict]:
    """requests.php's output, keeping only entries that name a bill."""
    lines = [l for l in text.strip().splitlines() if l.strip()]
    if not lines:
        raise Unavailable("requests.php printed nothing")
    try:
        rows = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise Unavailable(f"requests.php printed something else: "
                          f"{lines[-1][:200]}") from exc
    if not isinstance(rows, list):
        raise Unavailable("requests.php did not print a list")
    keep = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        session = str(r.get("session", ""))
        try:
            num = int(r.get("num", 0))
        except (TypeError, ValueError):
            continue
        if not SESSION_RE.match(session) or not 0 < num <= 999999:
            continue
        keep.append({"session": session, "num": num,
                     "first": str(r.get("first", "")),
                     "last": str(r.get("last", "")),
                     "n": int(r.get("n", 1) or 1)})
    return keep


def _known(db, session: str, num: int) -> bool:
    return db.execute("SELECT 1 FROM bill WHERE session=? AND num=?",
                      (session, num)).fetchone() is not None


def _analysed(db, session: str, num: int) -> bool:
    return db.execute("SELECT 1 FROM analysis_ai WHERE session=? AND num=?",
                      (session, num)).fetchone() is not None


def _tries(db) -> dict[tuple[str, int], int]:
    return {(r["session"], r["num"]): r["tries"] for r in
            db.execute("SELECT session, num, tries FROM request_try")}


def queue(db, requests: list[dict], limit: int = 0) -> list[dict]:
    """The requested bills to analyse tonight, in the order to do them.

    Only bills this machine has and has not analysed. Oldest request first,
    except that a bill which has already failed sinks below the ones that
    have not, so one bill the model cannot do does not hold up the rest.
    """
    tries = _tries(db)
    todo = [r for r in requests
            if _known(db, r["session"], r["num"])
            and not _analysed(db, r["session"], r["num"])]
    todo.sort(key=lambda r: (tries.get((r["session"], r["num"]), 0),
                             r["first"], r["session"], r["num"]))
    return todo[:limit] if limit else todo


def record_try(db, session: str, num: int) -> None:
    """A failed attempt, so tomorrow's queue puts this bill after the rest."""
    db.execute(
        "INSERT INTO request_try (session, num, tries, last) VALUES (?,?,1,?)"
        " ON CONFLICT(session, num) DO UPDATE SET tries=tries+1, last=excluded.last",
        (session, num, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))


def answer(db, requests: list[dict], site: Path) -> dict | None:
    """The payload send.php answers requests from, or None when it has none.

    `ready` is the requested bills whose page in `site` -- the tree just
    published -- no longer offers the request button. `unknown` is requests
    for bills this machine has never seen, which only a hand-made post can
    produce; they are closed without a word. A bill not yet done is in
    neither and its requests stay open.

    The payload has no `bills` key on purpose. A send.php that predates
    this kind of payload skips one without `bills`; with it, it would mail
    the list to every subscriber as a weekly digest.
    """
    ready, unknown = [], []
    for r in requests:
        session, num = r["session"], r["num"]
        if not _known(db, session, num):
            unknown.append({"session": session, "num": num, "upto": r["last"]})
            continue
        path = f"{bill_dir(session)}/{num}.html"
        page = site / path
        if not page.exists() or FORM_MARK in page.read_text(encoding="utf-8"):
            continue
        b = db.execute("SELECT label, title FROM bill WHERE session=? AND num=?",
                       (session, num)).fetchone()
        ai = db.execute("SELECT one_line FROM analysis_ai WHERE session=? AND num=?",
                        (session, num)).fetchone()
        ready.append({
            "session": session,
            "num": num,
            "path": path,
            "label": b["label"] or "",
            "title": b["title"] or "",
            # Only a summary that passed verification, as in the digests.
            "one_line": (ai["one_line"] if ai and ai["one_line"] else ""),
            # The newest request this covers. send.php closes none made after
            # it, so a payload left in the folder cannot answer a request it
            # never saw.
            "upto": r["last"],
        })
    if not ready and not unknown:
        return None
    return {"product": "ready",
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "ready": ready, "unknown": unknown}


def write(doc: dict, out: Path, today: str) -> Path:
    """One payload a day. A second run the same day writes over the first,
    which is safe: anything the first named and send.php has not yet
    answered is still open, so the second names it again."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{today}-ready.json"
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False),
                    encoding="utf-8")
    return path
