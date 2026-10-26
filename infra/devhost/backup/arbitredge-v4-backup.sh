#!/usr/bin/env bash
# Nightly V4 dev backups (runs as arbitredge-dev via systemd). Keeps the last 7.
set -euo pipefail
B=/srv/arbitredge_v4/backups
ts=$(date -u +%Y%m%dT%H%M%SZ)
umask 077
pg_dump -Fc -d arbitredge_v4 -f "$B/pg_arbitredge_v4_$ts.dump.part" && mv "$B/pg_arbitredge_v4_$ts.dump.part" "$B/pg_arbitredge_v4_$ts.dump"
if [[ -d /srv/arbitredge_v4/repo/.git ]]; then
  git -C /srv/arbitredge_v4/repo bundle create "$B/repo_$ts.bundle" --all 2>/dev/null
fi
for pat in 'pg_arbitredge_v4_*.dump' 'repo_*.bundle'; do
  ls -1t $B/$pat 2>/dev/null | tail -n +8 | xargs -r rm -f --
done
echo "backup ok $ts"
