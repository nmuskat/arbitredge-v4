#!/bin/bash
# Starts automatic collection: runs `python -m collect_v4.run` every 5 minutes.
# Each run fetches odds only if one is due (cadence speeds up near kickoff).
# Runs only while the Mac is awake. Stop with: launchctl bootout gui/$(id -u)/com.arbitredge.v4.collector
set -euo pipefail
source "$(dirname "$0")/config.sh"
cat > "$AGENTS/$COLLECT_LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$COLLECT_LABEL</string>
  <key>ProgramArguments</key><array><string>$V4_PY</string><string>-m</string><string>collect_v4.run</string></array>
  <key>WorkingDirectory</key><string>$V4_REPO</string>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>$V4_HOME/logs/collector.log</string>
  <key>StandardOutPath</key><string>$V4_HOME/logs/collector.log</string>
</dict></plist>
PLIST
launchctl bootout "gui/$(id -u)/$COLLECT_LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AGENTS/$COLLECT_LABEL.plist"
echo "collection scheduled every 5 minutes (log: $V4_HOME/logs/collector.log)"
