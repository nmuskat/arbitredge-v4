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
for f in ledger.jsonl collector_state.json; do
  if grep -qx "$f" <<<"$assets"; then
    gh release download v4-state -p "$f" -D state --clobber
  else
    echo "$f not present yet (first run)"
  fi
done
