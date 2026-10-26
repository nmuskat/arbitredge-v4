#!/bin/bash
# mac/set_budget.sh MONTHLY [DAILY]  -- V4's credit caps (monthly can never exceed 6000, the approved limit).
set -euo pipefail
source "$(dirname "$0")/config.sh"
"$V4_PY" -I - "$GW_CONFIG" "${1:?monthly cap}" "${2:-}" <<'PY'
import json, os, sys
p, m, d = sys.argv[1], int(sys.argv[2]), sys.argv[3]
if not 0 < m <= 6000:
    sys.exit("monthly cap must be between 1 and 6000 (owner-approved V4 limit)")
c = json.load(open(p)); c["v4_monthly_cap"] = m
if d:
    c["v4_daily_cap"] = int(d)
json.dump(c, open(p + ".tmp", "w"), indent=2); os.replace(p + ".tmp", p); os.chmod(p, 0o600)
print("v4_monthly_cap =", c["v4_monthly_cap"], " v4_daily_cap =", c["v4_daily_cap"])
PY
launchctl kickstart -k "gui/$(id -u)/$GW_LABEL"
