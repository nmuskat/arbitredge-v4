#!/bin/bash
# Download the credit ledger and collector state from the 'v4-state' release.
# A MISSING ledger is only accepted on the very first run (asset not listed);
# if it is listed but cannot be downloaded the job FAILS -- never run with an
# empty ledger, which would forget this month's spending.
set -euo pipefail
mkdir -p state out
if ! gh release view v4-state >/dev/null 2>&1; then
  gh release create v4-state --prerelease --title "V4 state (do not delete)" \
    --notes "Credit ledger and collector state for ArbitrEdge V4. Managed by workflows; deleting it resets V4's credit accounting."
fi
assets=$(gh release view v4-state --json assets --jq '.assets[].name')
if ! grep -qx "ledger.jsonl" <<<"$assets"; then
  # Only a genuine first run may start with an empty ledger. If odds data or
  # collector state already exist, the ledger was lost: refuse rather than
  # silently resetting this month's credit accounting to zero.
  data_releases=$(gh release list --limit 200 --json tagName --jq '[.[].tagName | select(startswith("odds-data-"))] | length')
  if [[ "$data_releases" != 0 ]] || grep -qx "collector_state.json" <<<"$assets"; then
    echo "::error::credit ledger missing from release v4-state although collection history exists -- refusing to run"
    exit 1
  fi
  echo "ledger.jsonl not present yet (first run)"
fi
for f in ledger.jsonl collector_state.json; do
  if grep -qx "$f" <<<"$assets"; then
    gh release download v4-state -p "$f" -D state --clobber      # any download failure fails the job
  fi
done
# AP-poll snapshots decide which CFB kickoffs get closing-line fetches. Optional: without them every
# CFB kickoff counts (never fewer closes, only more spend, still inside the credit caps).
mkdir -p state/rankings
if gh release view cfb-rankings >/dev/null 2>&1; then
  gh release download cfb-rankings -p 'rankings_*.json' -D state/rankings --clobber \
    || echo "::warning::AP-poll snapshots could not be downloaded; every CFB kickoff counts this run"
fi
