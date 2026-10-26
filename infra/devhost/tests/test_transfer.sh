#!/usr/bin/env bash
# End-to-end tests for transfer/send_odds_key.sh and transfer/copy_prod_db.sh
# against a simulated production host (a local directory reached through a
# fake ssh). Fake secrets are generated at runtime.
set -uo pipefail
PKG="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok()  { echo "  PASS $*"; pass=$((pass+1)); }
bad() { echo "  FAIL $*"; fail=$((fail+1)); }
expect() { local d=$1; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }

cat > "$T/fake_ssh.sh" <<'S'
#!/usr/bin/env bash
# fake ssh: drop options and host, run the remote command locally with sh
while [[ "${1:-}" == -* ]]; do case "$1" in -o) shift 2;; *) shift;; esac; done
shift
exec sh -c "$*"
S
export PROD_SSH="bash $T/fake_ssh.sh" PROD="root@prod.test"
KEY=$(python3 -c 'import secrets,string; print("".join(secrets.choice(string.ascii_lowercase+string.digits) for _ in range(32)))')
OLDKEY=$(python3 -c 'import secrets,string; print("".join(secrets.choice(string.ascii_lowercase+string.digits) for _ in range(32)))')
mkdir -p "$T/prod/env" "$T/prod/backups" "$T/dev/etc" "$T/dev/import"
export ETC_DIR="$T/dev/etc" GW_CODE="$PKG/gateway" IMPORT_DIR="$T/dev/import" DEV_GROUP=root RESTART=0 ASSUME_YES=1

echo "== send_odds_key.sh"
printf 'DB_URL=x\nexport THE_ODDS_API_KEY="%s"\nOTHER=1\n' "$KEY" > "$T/prod/env/.env"
prod_before=$(sha256sum "$T/prod/env/.env")
out=$(PROD_ENV_FILE="$T/prod/env/.env" bash "$PKG/transfer/send_odds_key.sh" 2>&1); rc=$?
expect "installs successfully" test $rc -eq 0
expect "key file is 0600 root" test "$(stat -c '%a %U' "$ETC_DIR/odds_api_key")" = "600 root"
expect "installed value parses to the production key" test "$(python3 -I -c 'import sys; sys.path.insert(0,sys.argv[2]); import odds_gateway as g; print(g.read_key(sys.argv[1]))' "$ETC_DIR/odds_api_key" "$GW_CODE")" = "$KEY"
expect "key value never printed" bash -c "! grep -q '$KEY' <<<\"\$1\"" _ "$out"
expect "only the key line transferred (no other env vars)" bash -c "! grep -q 'DB_URL' '$ETC_DIR/odds_api_key'"
expect "production env file untouched" test "$(sha256sum "$T/prod/env/.env")" = "$prod_before"
printf 'THE_ODDS_API_KEY=not a key; echo pwned\n' > "$T/prod/env/bad.env"
rm -f "$ETC_DIR/odds_api_key"
out=$(PROD_ENV_FILE="$T/prod/env/bad.env" bash "$PKG/transfer/send_odds_key.sh" 2>&1); rc=$?
expect "malformed value refused" test $rc -ne 0
expect "nothing installed on refusal" test ! -e "$ETC_DIR/odds_api_key" -a ! -e "$ETC_DIR/odds_api_key.new"
printf 'THE_ODDS_API_KEY=%s\n' "$KEY" > "$ETC_DIR/odds_api_key"; chmod 600 "$ETC_DIR/odds_api_key"

echo "== copy_prod_db.sh"
mkdb() {  # path, extra text row
  python3 - "$1" "$2" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("CREATE TABLE odds_snapshots (id INTEGER PRIMARY KEY, book TEXT, price INTEGER)")
c.execute("CREATE TABLE errors (id INTEGER PRIMARY KEY, msg TEXT)")
c.executemany("INSERT INTO odds_snapshots (book, price) VALUES (?, ?)", [("dk", -110)] * 5000)
c.execute("INSERT INTO errors (msg) VALUES (?)", (sys.argv[2],))
c.commit(); c.execute("PRAGMA journal_mode=DELETE"); c.close()
PY
}
B="$T/prod/backups"
mkdb "$B/arbitredge_backup_20261024_090000.db" "old"
mkdb "$B/arbitredge_backup_20261025_090000.db" "GET /v4/sports/x/odds?apiKey=$KEY&regions=us failed; older url apiKey=$OLDKEY"
mkdb "$B/arbitredge_backup_20261026_090000.db" "in progress"
touch -d '-2 days' "$B/arbitredge_backup_20261024_090000.db"
touch -d '-30 hours' "$B/arbitredge_backup_20261025_090000.db"
touch -d '-2 minutes' "$B/arbitredge_backup_20261026_090000.db"   # too fresh: may still be being written
before=$(cd "$B" && sha256sum * ; stat -c '%n %Y' "$B"/*)
export PROD_BACKUP_DIR="$B" PROD_DB="$T/prod/arbitredge.db" RATE_MBPS=50

out=$(bash "$PKG/transfer/copy_prod_db.sh" --check 2>&1); rc=$?
expect "--check succeeds" test $rc -eq 0
expect "--check copies nothing" test -z "$(ls -A "$IMPORT_DIR")"
expect "picks newest COMPLETED backup (skips the 2-minute-old one)" grep -q "20261025_090000" <<<"$out"

out=$(bash "$PKG/transfer/copy_prod_db.sh" 2>&1); rc=$?
echo "$out" | sed 's/^/    | /' | tail -6
f=$(ls "$IMPORT_DIR"/arbitredge_prod_copy_*.db 2>/dev/null | head -1)
expect "copy succeeds" test $rc -eq 0 -a -n "$f"
expect "copy is 0440 (read-only)" test "$(stat -c %a "$f")" = 440
expect "sha256 verified message" grep -q "sha256 verified end to end" <<<"$out"
expect "exactly 2 redactions reported (current + older key)" grep -q '"redactions": 2' <<<"$out"
expect "current key redacted from copy" bash -c "! grep -qa '$KEY' '$f'"
expect "older apiKey= value redacted from copy" bash -c "! grep -qa '$OLDKEY' '$f'"
expect "copy still passes integrity check after redaction" test "$(python3 -c "import sqlite3,sys; print(sqlite3.connect('file:$f?mode=ro', uri=True).execute('PRAGMA integrity_check').fetchone()[0])")" = ok
expect "row data intact" test "$(python3 -c "import sqlite3; print(sqlite3.connect('file:$f?mode=ro', uri=True).execute('select count(*) from odds_snapshots').fetchone()[0])")" = 5000
expect "key never printed" bash -c "! grep -q '$KEY' <<<\"\$1\"" _ "$out"
expect "COPY json written" test -f "${f%.db}.json"
expect "no partial file left" test ! -e "$IMPORT_DIR/.incoming.part"
after=$(cd "$B" && sha256sum * ; stat -c '%n %Y' "$B"/*)
expect "production backups byte-identical and mtimes unchanged" test "$before" = "$after"
expect "nothing new created on production" test "$(ls "$T/prod")" = "$(printf 'backups\nenv')"

echo "== refusals"
rm -rf "$IMPORT_DIR"/*
mkdir -p "$T/empty"
out=$(PROD_BACKUP_DIR="$T/empty" bash "$PKG/transfer/copy_prod_db.sh" 2>&1); rc=$?
expect "no completed backup -> refused (exit 2), live DB never used" test $rc -eq 2
out=$(PROD_BACKUP_DIR="$T/nope" bash "$PKG/transfer/copy_prod_db.sh" 2>&1); rc=$?
expect "missing backup dir -> refused (exit 2)" test $rc -eq 2
mkdir -p "$T/corrupt"; cp "$B/arbitredge_backup_20261024_090000.db" "$T/corrupt/arbitredge_backup_x.db"
python3 - "$T/corrupt/arbitredge_backup_x.db" <<'PY'
import sys
with open(sys.argv[1], "r+b") as f:
    f.seek(4096); f.write(b"\xff" * 64)   # destroy page 2's b-tree header, keep the file header
PY
touch -d '-1 day' "$T/corrupt/arbitredge_backup_x.db"
out=$(PROD_BACKUP_DIR="$T/corrupt" bash "$PKG/transfer/copy_prod_db.sh" 2>&1); rc=$?
expect "corrupt backup -> quarantined (exit 3)" test $rc -eq 3
q=$(ls "$IMPORT_DIR"/QUARANTINE_* 2>/dev/null | head -1)
expect "quarantine file root-only 0400" test -n "$q" -a "$(stat -c %a "$q" 2>/dev/null)" = 400
rm -rf "$IMPORT_DIR"/*
mkdir -p "$T/pk"; mkdb "$T/pk/arbitredge_backup_y.db" "$(printf -- '-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n')"
touch -d '-1 day' "$T/pk/arbitredge_backup_y.db"
out=$(PROD_BACKUP_DIR="$T/pk" bash "$PKG/transfer/copy_prod_db.sh" 2>&1); rc=$?
expect "private key material -> quarantined (exit 3)" test $rc -eq 3
mkdir -p "$T/optguard"
out=$(bash -c "mkdir -p /opt/arbitredge && bash '$PKG/transfer/copy_prod_db.sh'; r=\$?; rmdir /opt/arbitredge; exit \$r" 2>&1); rc=$?
expect "refuses to run on a host with /opt/arbitredge" test $rc -eq 2

echo; echo "RESULT: $pass passed, $fail failed"
(( fail == 0 ))
