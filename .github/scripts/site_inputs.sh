#!/bin/bash
# Download everything the site is built from (already-collected data only; no API calls):
# every odds shard, the credit ledger, any AP-poll snapshots, and the synthetic-book ledger
# (release v4-book: immutable files, one per advance of a weekly run).
set -euo pipefail
# GitHub's API sometimes answers a single request with a temporary server error (HTTP 5xx) or drops the
# connection; one such blip used to fail the whole build. Retry those (and only those) a few times,
# with growing pauses. Any other failure (e.g. 404 for a release that does not exist yet) returns at once,
# exactly as before. Downloads retry with --clobber so a partly written file is replaced, never kept.
gh() {
  local attempt err rc
  [[ "${1:-} ${2:-}" == "release download" ]] && set -- release download --clobber "${@:3}"
  err=$(mktemp)
  for attempt in 1 2 3 4 5; do
    if command gh "$@" 2>"$err"; then rm -f -- "$err"; return 0; else rc=$?; fi
    if (( attempt < 5 )) && grep -qiE 'HTTP 5[0-9][0-9]|connection reset|timeout|EOF|TLS handshake' "$err"; then
      echo "GitHub temporary error (attempt $attempt of 5), retrying in $((attempt * 5)) s: $(head -c 300 "$err")" >&2
      sleep $((attempt * 5))
      continue
    fi
    cat "$err" >&2; rm -f -- "$err"; return "$rc"
  done
}
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
  fetch_all "v4-need-$wk" 'official_snap_*.json.gz' need '^official_snap_'      # Official Needs records (experimental)
  fetch_all "v4-need-$wk" 'v6need_*.json.gz' need '^v6need_'                   # NEED v6 frozen records (write-once check)
  fetch_all "v4-need-$wk" 'v7intel_*.json.gz' need '^v7intel_'                 # V7 market-intelligence frozen records
done
fetch_all v4-need-decisions 'decisions_*.json.gz' need '^decisions_'
fetch_all v4-need-decisions 'official_dec_*.json.gz' need '^official_dec_'
fetch_all v4-results 'results_*.json.gz' results '^results_'
mkdir -p settle
fetch_all v4-settlement 'settle_*.json.gz' settle '^settle_'
# previous build's sensitivity replays (a cache, not a record): reused for up to 3 h; absent = recompute
mkdir -p prev
gh release download v4-site -p sensitivity_cache.json -D prev 2>/dev/null || echo "no sensitivity cache yet"
echo "shards: $(find shards -name 'quotes_*.jsonl.gz' | wc -l), rankings snapshots: $(find rankings -name 'rankings_*.json' | wc -l), ledger files: $(find book -name 'book_*.json.gz' | wc -l), need files: $(find need -name '*.json.gz' | wc -l), results files: $(find results -name 'results_*' | wc -l)"
