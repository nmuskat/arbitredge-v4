#!/bin/bash
# mac/set_live.sh on|off   -- the master switch for real Odds API calls.
set -euo pipefail
source "$(dirname "$0")/config.sh"
case "${1:-}" in on) v=true ;; off) v=false ;; *) echo "usage: $0 on|off"; exit 64 ;; esac
"$V4_PY" -I - "$GW_CONFIG" "$v" <<'PY'
import json, os, sys
p, v = sys.argv[1], sys.argv[2] == "true"
c = json.load(open(p)); c["live_enabled"] = v
json.dump(c, open(p + ".tmp", "w"), indent=2); os.replace(p + ".tmp", p); os.chmod(p, 0o600)
print("live_enabled =", v)
PY
launchctl kickstart -k "gui/$(id -u)/$GW_LABEL"
