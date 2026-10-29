#!/bin/bash
# Download everything the site is built from (already-collected data only; no API calls):
# every odds shard, the credit ledger, any AP-poll snapshots, and the synthetic-book ledger
# (release v4-book: immutable files, one per advance of a weekly run).
set -euo pipefail
mkdir -p shards state rankings book
tags=$(gh api --paginate "repos/${GITHUB_REPOSITORY}/releases?per_page=100" --jq '.[].tag_name | select(startswith("odds-data-"))')
[[ -n "$tags" ]] || { echo "no odds shards published yet" >&2; exit 1; }
for tag in $tags; do
  gh release download "$tag" -p 'quotes_*.jsonl.gz' -D "shards/$tag"
done
gh release download v4-state -p ledger.jsonl -D state            # required: health figures come from it
if ! gh release download cfb-rankings -p 'rankings_*.json' -D rankings 2>/dev/null; then
  echo "no AP-poll snapshots yet: CFB Top-25 views will show UNAVAILABLE"
fi
# synthetic-book ledger: absent only before its first file; otherwise every file must arrive
if n_book=$(gh api "repos/${GITHUB_REPOSITORY}/releases/tags/v4-book" --jq '[.assets[] | select(.name | startswith("book_"))] | length' 2>/dev/null); then
  if (( n_book > 0 )); then
    gh release download v4-book -p 'book_*.json.gz' -D book
    got=$(find book -name 'book_*.json.gz' | wc -l)
    (( got == n_book )) || { echo "synthetic-book ledger incomplete: $got of $n_book files" >&2; exit 1; }
  fi
else
  echo "no synthetic-book ledger yet: this build starts it"
fi
# Book Need Tracker (immutable): raw snapshots of this and last ISO week, every sealed decision, final scores
mkdir -p need results
fetch_all() {   # $1 = release tag, $2 = asset pattern, $3 = destination; absent release = nothing yet
  local n got
  n=$(gh api "repos/${GITHUB_REPOSITORY}/releases/tags/$1" --jq "[.assets[] | select(.name | test(\"$4\"))] | length" 2>/dev/null) || return 0
  [[ "$n" =~ ^[0-9]+$ ]] || { echo "$1: unexpected asset count '$n'" >&2; exit 1; }
  (( n > 0 )) || return 0
  gh release download "$1" -p "$2" -D "$3/$1"
  got=$(find "$3/$1" -name "$2" | wc -l)
  (( got == n )) || { echo "$1 incomplete: $got of $n files" >&2; exit 1; }
}
for wk in "$(date -u +%G-W%V)" "$(date -u -d '7 days ago' +%G-W%V)"; do
  fetch_all "v4-need-$wk" 'need_*.json.gz' need '^need_'
done
fetch_all v4-need-decisions 'decisions_*.json.gz' need '^decisions_'
fetch_all v4-results 'results_*.json.gz' results '^results_'
echo "shards: $(find shards -name 'quotes_*.jsonl.gz' | wc -l), rankings snapshots: $(find rankings -name 'rankings_*.json' | wc -l), ledger files: $(find book -name 'book_*.json.gz' | wc -l), need files: $(find need -name '*.json.gz' | wc -l), results files: $(find results -name 'results_*' | wc -l)"
