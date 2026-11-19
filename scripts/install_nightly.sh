#!/usr/bin/env bash
# Schedule scripts/nightly.sh on this Mac with launchd.
#
#   scripts/install_nightly.sh            # every day at 05:00
#   scripts/install_nightly.sh 4 30       # every day at 04:30
#   scripts/install_nightly.sh --remove   # unschedule
#
# A LaunchAgent runs in the logged-in user's session, which is also where
# oMLX runs, so the Mac has to be logged in for either to work. A run that
# falls while the Mac is asleep happens when it wakes.
#
# Run one now, without waiting for the hour:
#
#   launchctl kickstart gui/$(id -u)/org.knowyourlegislation.nightly
#
# The checkout must not be under ~/Documents, ~/Desktop or ~/Downloads.
# macOS keeps scheduled jobs out of those folders: the job starts, then
# cannot read its own code. This refuses rather than install one that fails.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
case "$ROOT" in
  "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*)
    echo "refusing: $ROOT is in a folder macOS keeps scheduled jobs out of." >&2
    echo "move the checkout (e.g. to ~/Projects) and run this again from there." >&2
    exit 2 ;;
esac
LABEL="org.knowyourlegislation.nightly"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if [[ "${1:-}" == "--remove" ]]; then
  rm -f "$PLIST"
  echo "removed $LABEL"
  exit 0
fi

HOUR="${1:-5}"
MINUTE="${2:-0}"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$ROOT/scripts/nightly.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>$HOUR</integer>
    <key>Minute</key>
    <integer>$MINUTE</integer>
  </dict>
  <!-- Anything the script prints before its own log is open. -->
  <key>StandardOutPath</key>
  <string>$HOME/Library/Logs/know-your-legislation-nightly.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/Library/Logs/know-your-legislation-nightly.log</string>
  <key>ProcessType</key>
  <string>Standard</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$DOMAIN" "$PLIST"
printf 'scheduled %s daily at %02d:%02d\n' "$LABEL" "$HOUR" "$MINUTE"
echo "logs: $ROOT/data/logs/nightly-YYYY-MM-DD.log"
