#!/usr/bin/env bash
# The production run, start to finish, on the analysis box.
#
#   1. Ingest   new bills, and bills that moved, from flsenate.gov
#   2. Analyse  bills readers asked for on the site, any session, first;
#               then new bills, and bills with a newer version
#   3. Publish  the site to the public host
#   4. Daily    write yesterday's alert and publish it; send.php mails it
#   5. Weekly   on Mondays, the same for the seven days before
#   6. Answer   the requests whose bill is now analysed on the live site;
#               send.php writes to whoever asked
#
# Scheduled by deploy/launchd/org.knowyourlegislation.nightly.plist. Run it by
# hand the same way:
#
#   scripts/nightly.sh              # the whole thing
#   scripts/nightly.sh --no-deploy  # everything but the two pushes
#
# Each stage is resumable and skips work already done, so a run that dies
# halfway is finished by the next one. One stage failing does not stop the
# ones after it unless they cannot work without it; the run exits non-zero
# and the log says which.
#
# The public site is built into .working/site-public, not site/. site/ is the
# operator build that scripts/serve_local.py serves, with the re-run and edit
# controls, and deploy.sh refuses to push it.
set -uo pipefail

# -P: the real folder, never a link to it. A link from ~/Documents would put
# every path back inside the folder macOS keeps scheduled jobs out of.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"

export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONPATH="$ROOT/src"

SESSION="${FLBA_SESSION:-2027}"
DEPLOY=1
[[ "${1:-}" == "--no-deploy" ]] && DEPLOY=0

PUBLIC="$ROOT/.working/site-public"
NEXT="$ROOT/.working/site-next"
LOGS="$ROOT/data/logs"
mkdir -p "$LOGS" "$ROOT/.working" "$ROOT/data/backup"
LOG="$LOGS/nightly-$(date +%F).log"
exec >>"$LOG" 2>&1

# One run at a time. mkdir is atomic; a lock left by a run that was killed
# is cleared once the process that took it is gone.
LOCK="$ROOT/.working/nightly.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if kill -0 "$(cat "$LOCK/pid" 2>/dev/null)" 2>/dev/null; then
    echo "[$(date '+%F %T')] another run is going (pid $(cat "$LOCK/pid")); not starting"
    exit 0
  fi
  rm -rf "$LOCK" && mkdir "$LOCK"
fi
echo $$ >"$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

FAILED=()
flba() { "$ROOT/.venv/bin/python" -u -m flba --session "$SESSION" "$@"; }
say()  { echo; echo "[$(date '+%F %T')] == $*"; }
step() {               # step <name> <command...>: run it, note a failure
  local name="$1"; shift
  say "$name"
  "$@"
  local rc=$?
  if (( rc != 0 )); then
    echo "!! $name exited $rc"
    FAILED+=("$name")
  fi
  return $rc
}
finish() {
  say "done: ${#FAILED[@]} step(s) failed${FAILED[*]:+: ${FAILED[*]}}"
  if (( ${#FAILED[@]} )); then
    osascript -e "display notification \"Failed: ${FAILED[*]}\" with title \"Know Your Legislation nightly\"" \
      >/dev/null 2>&1 || true
    exit 1
  fi
  exit 0
}

say "nightly run, session $SESSION, deploy=$DEPLOY"

# A copy of the database before anything writes to it. A re-analysis replaces
# a row outright, hand edits included; this is the way back. Seven are kept.
sqlite3 data/index.sqlite ".backup data/backup/index-$(date +%F).sqlite" \
  || FAILED+=("backup")
ls -1t data/backup/index-20*.sqlite 2>/dev/null | tail -n +8 | xargs rm -f

# ---------------------------------------------------------------- 1. ingest
step "enumerate" flba enumerate --refresh
listed=$("$ROOT/.venv/bin/python" -c "import json,sys; print(json.load(open(sys.argv[1]))['scraped_total'])" \
  "data/index/$SESSION.json" 2>/dev/null || echo 0)
if (( listed == 0 )); then
  # An empty list is a failed fetch, not a session with no bills. Publishing
  # from it would change nothing, but nothing after this can be trusted.
  echo "!! the bill list came back empty; stopping before anything is published"
  FAILED+=("empty-list")
  finish
fi
step "bills"     flba bills --changed
step "docs"      flba docs
step "vote pdfs" flba docs --kinds votes --delay 3
step "crossref"  flba crossref
step "votes"     flba votes
step "members"   flba members
step "finance"   flba finance

# --------------------------------------------------------------- 2. analyse
# Both exit 2 when the model server does not answer. The run carries on
# without it: the site and the alert still go out, and the bills wait for
# tomorrow.
#
# Requests first: someone is waiting on those. They are read from the public
# host over SSH (exit 3 if it cannot be reached) and can be for any session.
# Capped, so a burst of them cannot crowd out the night's new bills.
step "requests"  flba requests analyze --limit 25
step "analyze"   flba analyze --stale --order number

# --------------------------------------------------------------- 3. publish
# Built fresh each night so a page that should no longer exist does not
# linger in the tree that deploy.sh mirrors. The payloads already written
# are carried over: the public host keeps its own, but this is the record.
rm -rf "$NEXT"
if step "build" flba build --out "$NEXT"; then
  [[ -d "$PUBLIC/mail" ]] && cp -Rp "$PUBLIC/mail" "$NEXT/"
  # Always there, even empty: send.php exits with an error every five
  # minutes when the folder is missing on the public host.
  mkdir -p "$NEXT/mail"
  rm -rf "$ROOT/.working/site-prev"
  [[ -d "$PUBLIC" ]] && mv "$PUBLIC" "$ROOT/.working/site-prev"
  mv "$NEXT" "$PUBLIC"
else
  echo "!! build failed; publishing nothing tonight"
  finish
fi
# The operator pages, so the re-run and edit controls show tonight's bills.
step "build --local" flba build --local

deploy() { FLBA_SRC="$PUBLIC/" "$ROOT/scripts/deploy.sh" --go; }

if (( DEPLOY )); then
  # Pages first. The alert links to them, and send.php mails a payload the
  # moment it lands, so the pages have to be there before it does.
  if ! step "deploy site" deploy; then
    echo "!! not writing tonight's alert: the pages it links to are not up"
    finish
  fi
fi

# ------------------------------------------------------ 4 & 5. daily, weekly
step "daily digest" flba digest --product daily --out "$PUBLIC/mail"
if [[ "$(date +%u)" == 1 ]]; then
  step "weekly digest" flba digest --product weekly --out "$PUBLIC/mail"
fi

if (( DEPLOY )); then
  # ------------------------------------------------------------- 6. answer
  # Only once the pages are up, and read off the tree that just went up: a
  # request is answered when its bill's live page no longer offers the
  # button, which is what the person who asked will find.
  step "answer requests" flba requests answer --site "$PUBLIC" --out "$PUBLIC/mail"

  # Only the new payloads change, so this is quick. send.php picks them up
  # on its next five-minute pass.
  step "deploy mail" deploy
fi

# Logs older than sixty days.
find "$LOGS" -name 'nightly-*.log' -mtime +60 -delete 2>/dev/null

finish
