#!/bin/bash
# Budget, breaker and collection status (no secrets shown).
source "$(dirname "$0")/config.sh"
cd "$V4_REPO" && "$V4_PY" -m collect_v4.run --status
echo; tail -n 5 "$V4_HOME/logs/collector.log" 2>/dev/null
