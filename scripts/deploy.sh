#!/usr/bin/env bash
# Push the built site to the public host over SSH. One-way, by design.
#
# The public server never connects back to this machine, holds no model and no
# pipeline code -- only the built files, six PHP endpoints and a subscriber
# database. That is what makes the gap between the analysis box and the public
# site structural rather than merely configured.
#
# Usage:
#   scripts/deploy.sh                 # dry run -- prints what WOULD change
#   scripts/deploy.sh --go            # actually push
#
# Configure via environment (or a .env file kept out of git):
#   FLBA_HOST=ubuntu@51.81.49.136
#   FLBA_PATH=/srv/sessionwatch/site
#
# This is the path for a host that gives a shell. scripts/deploy.py is the
# SFTP path, for shared hosting that sells file access without one.
set -euo pipefail

# Run from anywhere. The paths below are the repository's, not the caller's.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# .env, without overriding a variable already in the environment -- the same
# precedence scripts/deploy.py uses, so the two cannot disagree about where
# they are pushing.
if [[ -f .env ]]; then
  while IFS= read -r line; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    key="${line%%=*}"
    val="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"
    [[ -z "${!key-}" ]] && export "$key=$val"
  done < .env
fi

SRC="${FLBA_SRC:-site/}"
HOST="${FLBA_HOST:-}"
DEST="${FLBA_PATH:-}"

if [[ -z "$HOST" || -z "$DEST" ]]; then
  echo "set FLBA_HOST and FLBA_PATH first, e.g.:" >&2
  echo "  export FLBA_HOST=ubuntu@51.81.49.136" >&2
  echo "  export FLBA_PATH=/srv/sessionwatch/site" >&2
  exit 2
fi
if [[ ! -f "$SRC/index.html" ]]; then
  echo "no built site at $SRC -- run: flba --session 2026 build" >&2
  exit 2
fi

# A --local build carries operator controls which run the model. They post to a
# server on the analysis box and would be dead links in public at best. The gap
# between the two machines is the point, so this refuses rather than warns.
if [[ -f "$SRC/.local-build" ]]; then
  echo "refusing to deploy: $SRC was built with --local." >&2
  echo "that tree carries operator controls which run the analyzer." >&2
  echo "rebuild without it:  flba --session 2026 build" >&2
  exit 3
fi

DRY="--dry-run"
[[ "${1:-}" == "--go" ]] && DRY=""

if [[ -n "$DRY" ]]; then
  echo "DRY RUN -- nothing will be written. Re-run with --go to push."
fi

# --delete keeps the public tree an exact mirror, so bills withdrawn from the
# source do not linger. --checksum because a rebuild rewrites every file's
# mtime even when the bytes are identical.
#
# Two things in the destination are not in the source and must survive it:
#
#   config.php   holds the database password and the secret signing every
#                manage and unsubscribe link. It is uploaded once by hand.
#                Deleting it takes the site down and rotating it invalidates
#                every link already emailed.
#   mail/        digest payloads. send.php reads them from here and does not
#                remove them. A payload deleted before its cron run is a
#                digest nobody receives, and nothing reports it.
#
# --filter 'protect' is what keeps --delete off them. --exclude alone would
# skip sending the file but still let the delete pass remove it.
rsync -az --checksum --delete $DRY \
  --exclude '.DS_Store' \
  --filter 'protect config.php' \
  --filter 'protect mail/' \
  --human-readable --stats \
  "$SRC" "$HOST:$DEST/"

if [[ -z "$DRY" ]]; then
  echo
  echo "pushed. Nothing on the public host runs code from this machine."
fi
