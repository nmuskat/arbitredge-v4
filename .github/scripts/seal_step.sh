#!/bin/bash
# Seal Need-tracker decisions for games that kicked off (no secrets; runs inside the odds-collect job).
# Does nothing (no downloads) unless a priority kickoff passed recently. Uploads new files only, never overwrites.
set -euo pipefail
state=out/collector_state.json
[[ -f "$state" ]] || state=state/collector_state.json
[[ -f "$state" ]] || { echo "no collector state: nothing to seal"; exit 0; }
if [[ "$(python3 -m sportsbook_v4.seal_run due --state "$state")" != "yes" ]]; then
  echo "no kickoff to seal"; exit 0
fi
mkdir -p need need_new
for tag in "v4-need-$(date -u +%G-W%V)" "v4-need-$(date -u -d '7 days ago' +%G-W%V)" v4-need-decisions; do
  gh release download "$tag" -p '*.json.gz' -D "need/$tag" 2>/dev/null || true
done
python3 -m sportsbook_v4.seal_run seal --state "$state" --need-dir need --out need_new
shopt -s nullglob
files=(need_new/v4-need-decisions/decisions_*.json.gz need_new/v4-need-decisions/official_dec_*.json.gz)
(( ${#files[@]} )) || exit 0
gh release view v4-need-decisions >/dev/null 2>&1 || gh release create v4-need-decisions --prerelease \
  --title "V4 Book Need Tracker: v4-need-decisions (do not delete)" --notes "Immutable MODELED research records."
gh release upload v4-need-decisions "${files[@]}"
