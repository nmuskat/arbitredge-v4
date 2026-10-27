#!/bin/bash
# Download everything the site is built from (already-collected data only; no API calls):
# every odds shard, the credit ledger, and any AP-poll snapshots.
set -euo pipefail
mkdir -p shards state rankings
tags=$(gh api --paginate "repos/${GITHUB_REPOSITORY}/releases?per_page=100" --jq '.[].tag_name | select(startswith("odds-data-"))')
[[ -n "$tags" ]] || { echo "no odds shards published yet" >&2; exit 1; }
for tag in $tags; do
  gh release download "$tag" -p 'quotes_*.jsonl.gz' -D "shards/$tag"
done
gh release download v4-state -p ledger.jsonl -D state            # required: health figures come from it
if ! gh release download cfb-rankings -p 'rankings_*.json' -D rankings 2>/dev/null; then
  echo "no AP-poll snapshots yet: CFB Top-25 views will show UNAVAILABLE"
fi
echo "shards: $(find shards -name 'quotes_*.jsonl.gz' | wc -l), rankings snapshots: $(find rankings -name 'rankings_*.json' | wc -l)"
