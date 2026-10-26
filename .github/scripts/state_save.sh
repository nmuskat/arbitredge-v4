#!/bin/bash
# Upload this run's quote shard (weekly release odds-data-YYYY-Www) and the
# updated ledger/state (v4-state). Shards are written once and never modified.
set -euo pipefail
shopt -s nullglob
shards=(out/quotes_*.jsonl.gz)
if (( ${#shards[@]} )); then
  tag="odds-data-$(date -u +%G-W%V)"
  gh release view "$tag" >/dev/null 2>&1 || gh release create "$tag" --prerelease --title "$tag" \
    --notes "Timestamped multi-book odds collected by ArbitrEdge V4 (The Odds API). One file per collection run."
  gh release upload "$tag" "${shards[@]}"
fi
if [[ -f out/ledger.jsonl ]]; then
  gh release upload v4-state out/ledger.jsonl out/collector_state.json --clobber
fi
if [[ -f out/run_summary.json ]]; then
  python3 -c 'import json; s=json.load(open("out/run_summary.json")); print(json.dumps({k: s.get(k) for k in ("status","fetches","skipped","refused","usage","caps")}, indent=1, default=str))'
fi
