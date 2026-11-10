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
  # triggers the site rebuild (the expanded tier throttles rebuilds: out/site_skip)
  if [[ -n "${GITHUB_OUTPUT:-}" && ! -f out/site_skip ]]; then echo "fetched=true" >> "$GITHUB_OUTPUT"; fi
fi
# one-time historical backfill (owner-started): its snapshots go to their OWN release, never into
# odds-data-* (the live site and live book never read them); written once, never modified
hist=(out/hquotes_*.jsonl.gz)
if (( ${#hist[@]} )); then
  gh release view odds-hist >/dev/null 2>&1 || gh release create odds-hist --prerelease --title "odds-hist" \
    --notes "Historical odds snapshots (The Odds API historical endpoint) for the one-time season backfill. Do not delete."
  gh release upload odds-hist "${hist[@]}"
fi
if [[ -f out/ledger.jsonl ]]; then
  state_files=(out/ledger.jsonl)
  [[ -f out/collector_state.json ]] && state_files+=(out/collector_state.json)
  [[ -f out/backfill_state.json ]] && state_files+=(out/backfill_state.json)
  gh release upload v4-state "${state_files[@]}" --clobber
fi
if [[ -f out/run_summary.json ]]; then
  python3 -c 'import json; s=json.load(open("out/run_summary.json")); print(json.dumps({k: s.get(k) for k in ("status","mode","fetches","fetched","skipped","refused","usage","caps","allowance","allowance_remaining","credits_spent_this_run","complete")}, indent=1, default=str))'
fi
