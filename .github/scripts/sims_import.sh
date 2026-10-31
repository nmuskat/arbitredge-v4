#!/bin/bash
# Import ArbitrEdge SIMS web exports (NFL) into this build, read-only. Never fails the build.
#   sims/      : the newest already-imported export of each kind (release sims-import of THIS repository, GITHUB_TOKEN)
#   new ones   : only files not yet imported are downloaded from release web-export of nmuskat/arbitredge-sims with
#                SIMS_TOKEN (fine-grained token, Contents: Read-only, that repository only), validated by
#                sims_v5.importer and copied to release sims-import (new files only, never overwritten).
# Cost when nothing is new: two release listings. Each network call is limited to 20 s.
# SIMS_TOKEN is used for the SIMS repository calls only and is never printed. Without it (or if it is expired or
# revoked, or SIMS is unreachable) the build uses the already-imported exports. No Odds API, no simulation, no write
# to SIMS.
set -uo pipefail
T="timeout ${SIMS_NET_TIMEOUT_S:-20}"
mkdir -p sims sims_new sims_ok
have=$($T gh api "repos/${GITHUB_REPOSITORY}/releases/tags/sims-import" --jq '.assets[].name' 2>/dev/null) || have=""
for kind in projections edges history; do               # newest imported file of each kind (names end in UTC time)
  n=$(printf '%s\n' $have | grep "^sims_nfl_${kind}_.*\.json\.gz$" | sed -E 's/.*_([0-9]{8}T[0-9]{6}Z)\.json\.gz$/\1 &/' \
      | sort | tail -1 | cut -d' ' -f2)
  [[ -n "$n" ]] && { $T gh release download sims-import -p "$n" -D sims 2>/dev/null || echo "could not fetch imported $n"; }
done
if [[ -z "${SIMS_TOKEN:-}" ]]; then
  echo "SIMS read token not configured: using already imported exports only"
  exit 0
fi
names=$(GH_TOKEN="$SIMS_TOKEN" $T gh api repos/nmuskat/arbitredge-sims/releases/tags/web-export \
          --jq '.assets[].name' 2>/dev/null) || { echo "SIMS export release unavailable: keeping imported exports"; exit 0; }
new=0
for n in $names; do
  [[ "$n" =~ ^sims_nfl_[A-Za-z0-9_.-]+\.json\.gz$ ]] || continue
  printf '%s\n' $have | grep -qxF "$n" && continue        # already imported: nothing to download
  GH_TOKEN="$SIMS_TOKEN" $T gh release download web-export -R nmuskat/arbitredge-sims -p "$n" -D sims_new 2>/dev/null \
    && new=$((new + 1)) || echo "could not download $n (skipped)"
done
(( new )) || { echo "no new SIMS exports"; exit 0; }
python3 -m sims_v5.importer accept --in sims_new --have sims --out sims_ok || true
shopt -s nullglob
files=(sims_ok/sims_nfl_*.json.gz)
(( ${#files[@]} )) || { echo "no new valid SIMS exports"; exit 0; }
$T gh release view sims-import >/dev/null 2>&1 || $T gh release create sims-import --prerelease \
  --title "SIMS exports imported into V5 (do not delete)" \
  --notes "Validated, unchanged copies of ArbitrEdge SIMS web exports (NFL, MODELED). New files only." || true
$T gh release upload sims-import "${files[@]}" || echo "upload failed: these exports are shown this build only"
cp "${files[@]}" sims/
