#!/usr/bin/env bash
# Push the built site to the public host. One-way, by design.
#
# The public server never connects back to this machine, holds no model, no
# pipeline and no database -- only files. That is what makes the gap between
# the analysis box and the public site structural rather than merely
# configured.
#
# Usage:
#   scripts/deploy.sh                 # dry run -- prints what WOULD change
#   scripts/deploy.sh --go            # actually push
#
# Configure via environment (or a .env file kept out of git):
#   FLBA_HOST=user@example.com
#   FLBA_PATH=/homepages/NN/htdocs/billalert
set -euo pipefail

SRC="${FLBA_SRC:-site/}"
HOST="${FLBA_HOST:-}"
DEST="${FLBA_PATH:-}"

if [[ -z "$HOST" || -z "$DEST" ]]; then
  echo "set FLBA_HOST and FLBA_PATH first, e.g.:" >&2
  echo "  export FLBA_HOST=u12345@access.example.com" >&2
  echo "  export FLBA_PATH=/homepages/12/htdocs/billalert" >&2
  exit 2
fi
if [[ ! -f "$SRC/index.html" ]]; then
  echo "no built site at $SRC -- run: flba --session 2026 build" >&2
  exit 2
fi

# A --local build carries buttons that run the model. They post to a server on
# the analysis box and would be dead links in public at best. The gap between
# the two machines is the point, so this refuses rather than warns.
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
rsync -az --checksum --delete $DRY \
  --exclude '.DS_Store' \
  --human-readable --stats \
  "$SRC" "$HOST:$DEST/"

if [[ -z "$DRY" ]]; then
  echo
  echo "pushed. Nothing on the public host runs code from this machine."
fi
