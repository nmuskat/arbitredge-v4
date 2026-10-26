#!/bin/bash
# Stores the Odds API key in your macOS login Keychain. You paste the key at a
# HIDDEN prompt (nothing shows while you paste -- that is normal), then again to confirm.
# The key is never displayed, never written to a file, and never committed to GitHub.
set -euo pipefail
source "$(dirname "$0")/config.sh"
echo "Paste your Odds API key and press Return (nothing will appear on screen)."
/usr/bin/security add-generic-password -U -a "$KEYCHAIN_ACCOUNT" -s "$KEYCHAIN_SERVICE" -l "ArbitrEdge V4 Odds API key" -w
len=$("$V4_PY" -I -c '
import subprocess, sys
sys.path.insert(0, sys.argv[1])
import odds_gateway as g
k = g.key_from_keychain(sys.argv[2])
print(len(k) if k else 0)' "$V4_REPO/infra/devhost/gateway" "$KEYCHAIN_SERVICE" 2>/dev/null || echo 0)
if [[ "$len" == 0 ]]; then
  echo "The key was saved but does not look like an Odds API key. Run this script again and re-paste it."
  exit 1
fi
launchctl kickstart -k "gui/$(id -u)/$GW_LABEL" 2>/dev/null || true
echo "Key saved in your Keychain ($len characters, value not shown)."
