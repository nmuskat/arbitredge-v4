#!/usr/bin/env bash
# Survey must be read-only and must redact secrets.
set -uo pipefail
PKG="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
pass=0; fail=0
expect() { local d=$1; shift; if "$@"; then echo "  PASS $d"; pass=$((pass+1)); else echo "  FAIL $d"; fail=$((fail+1)); fi; }
KEY=$(python3 -c 'import secrets,string; print("".join(secrets.choice(string.ascii_lowercase+string.digits) for _ in range(32)))')
mkdir -p "$T/app/db/backups"
python3 -c "import sqlite3; c=sqlite3.connect('$T/app/db/arbitredge.db'); c.execute('pragma journal_mode=wal'); c.execute('create table t(x)'); c.commit()"
for d in 20261020 20261021; do cp "$T/app/db/arbitredge.db" "$T/app/db/backups/arbitredge_backup_${d}_090000.db"; done
printf '2026-10-26 backup FAILED: disk full; THE_ODDS_API_KEY=%s url=https://x/?apiKey=%s\n' "$KEY" "$KEY" > "$T/app/db/backups/backup.log"
snap() { (cd "$T" && find . -printf '%p %s %T@ %m\n' | sort; sha256sum app/db/*.db app/db/backups/* 2>/dev/null); }
before=$(snap)
out=$(APP="$T/app" bash "$PKG/prod_readonly/prod_disk_survey.sh" 2>&1); rc=$?
expect "survey completes" test $rc -eq 0
expect "application tree unchanged (no files created/modified, no -wal/-shm opened)" test "$before" = "$(snap)"
expect "secret values redacted" bash -c "! grep -q '$KEY' <<<\"\$1\"" _ "$out"
expect "redaction marker present" grep -q 'THE_ODDS_API_KEY=\[REDACTED\]' <<<"$out"
expect "WAL mode reported from header" grep -q 'header bytes 18-19: 2 2' <<<"$out"
expect "backup count reported" grep -q 'backup count: 2' <<<"$out"
expect "script contains no write/delete commands" bash -c "! grep -nE '(^|[;&| ])(rm|mv|cp|truncate|dd|tee|chmod|chown|apt(-get)?|systemctl (restart|stop|start)|journalctl --vacuum|sqlite3) ' '$PKG/prod_readonly/prod_disk_survey.sh'"
echo; echo "RESULT: $pass passed, $fail failed"; (( fail == 0 ))
