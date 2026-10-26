#!/usr/bin/env bash
# Runs devhost_bootstrap.sh for real (users, groups, directories, permissions)
# in a DISPOSABLE container, with systemd/ufw/sshd/apt/postgres steps skipped.
# DESTRUCTIVE to the container's /srv/arbitredge_v4 etc. — refuses unless
# ALLOW_CONTAINER_BOOTSTRAP_TEST=1.
set -uo pipefail
[[ "${ALLOW_CONTAINER_BOOTSTRAP_TEST:-0}" == 1 ]] || { echo "set ALLOW_CONTAINER_BOOTSTRAP_TEST=1 (disposable container only)"; exit 64; }
PKG="$(cd "$(dirname "$0")/.." && pwd)"
export CONTAINER_TEST=1 ASSUME_YES=1 MIN_DISK_GB=5 MANAGED_SETTINGS=/tmp/cc-test/managed-settings.json
pass=0; fail=0
expect() { local d=$1; shift; if "$@"; then echo "  PASS $d"; pass=$((pass+1)); else echo "  FAIL $d"; fail=$((fail+1)); fi; }
clean() {
  for u in arbitredge-dev arbitredge-gw; do id "$u" >/dev/null 2>&1 && userdel -r "$u" >/dev/null 2>&1; done
  getent group arbitredge-gwsock >/dev/null && groupdel arbitredge-gwsock
  rm -rf /srv/arbitredge_v4 /opt/arbitredge-v4-gateway /etc/arbitredge-v4 /var/lib/arbitredge-v4-gateway \
         /usr/local/sbin/arbitredge-v4-gateway-admin /tmp/cc-test /home/arbitredge-dev
}
state() { find /srv /opt /etc/arbitredge-v4 /var/lib/arbitredge-v4-gateway -maxdepth 3 2>/dev/null | sort; getent passwd | grep -c arbitredge; }

echo "== refusals change nothing"
clean
s0=$(state)
out=$(PROD_IP=$(hostname -I | awk '{print $1}') bash "$PKG/devhost_bootstrap.sh" 2>&1); rc=$?
expect "refuses on the production IP (exit 2)" test $rc -eq 2
expect "  ...message names production" grep -q "PRODUCTION IP" <<<"$out"
mkdir /opt/arbitredge; out=$(bash "$PKG/devhost_bootstrap.sh" 2>&1); rc=$?; rmdir /opt/arbitredge
expect "refuses when /opt/arbitredge exists" test $rc -eq 2
out=$(MIN_RAM_MB=999999 bash "$PKG/devhost_bootstrap.sh" 2>&1); rc=$?
expect "refuses on insufficient RAM (hard stop, not a warning)" test $rc -eq 2
out=$(MIN_DISK_GB=999999 bash "$PKG/devhost_bootstrap.sh" 2>&1); rc=$?
expect "refuses on insufficient disk" test $rc -eq 2
useradd -m arbitredge-dev; out=$(bash "$PKG/devhost_bootstrap.sh" 2>&1); rc=$?; userdel -r arbitredge-dev >/dev/null 2>&1
expect "refuses a pre-existing user it did not create" test $rc -eq 2
expect "no refusal changed anything" test "$s0" = "$(state)"
out=$(bash "$PKG/devhost_bootstrap.sh" --check 2>&1); rc=$?
expect "--check passes and changes nothing" test $rc -eq 0 -a "$s0" = "$(state)"

echo "== fresh install"
out=$(bash "$PKG/devhost_bootstrap.sh" 2>&1); rc=$?
expect "bootstrap succeeds (includes verify_devhost.sh)" test $rc -eq 0
expect "verification reported 0 failures" grep -q "RESULT: .* 0 failed" <<<"$out"
expect "repo cloned at v4-dev with edge_v4" test -f /srv/arbitredge_v4/repo/edge_v4/odds_client.py
expect "dev user owns repo" test "$(stat -c %U /srv/arbitredge_v4/repo)" = arbitredge-dev
expect "gateway state dir 0700 gw-only" test "$(stat -c '%U %a' /var/lib/arbitredge-v4-gateway)" = "arbitredge-gw 700"
expect "gateway.json live_enabled=false" python3 -c "import json,sys; sys.exit(json.load(open('/etc/arbitredge-v4/gateway.json'))['live_enabled'] is not False)"

echo "== idempotent re-run keeps owner edits"
python3 - <<'PY'
import json; p='/etc/arbitredge-v4/gateway.json'; c=json.load(open(p)); c['v4_daily_cap']=123; json.dump(c,open(p,'w'))
PY
out=$(bash "$PKG/devhost_bootstrap.sh" 2>&1); rc=$?
expect "re-run succeeds" test $rc -eq 0
expect "owner config edit preserved" python3 -c "import json,sys; sys.exit(json.load(open('/etc/arbitredge-v4/gateway.json'))['v4_daily_cap']!=123)"

echo "== verify catches tampering"
chmod 0644 /etc/arbitredge-v4/gateway.json
out=$(bash "$PKG/verify_devhost.sh" 2>&1); rc=$?
expect "world-readable gateway config detected" test $rc -ne 0
chmod 0640 /etc/arbitredge-v4/gateway.json
usermod -a -G arbitredge-gw arbitredge-dev
out=$(bash "$PKG/verify_devhost.sh" 2>&1); rc=$?
expect "dev user added to gateway group detected" test $rc -ne 0
gpasswd -d arbitredge-dev arbitredge-gw >/dev/null
KEY=$(python3 -c 'import secrets,string; print("".join(secrets.choice(string.ascii_lowercase+string.digits) for _ in range(32)))')
printf 'THE_ODDS_API_KEY=%s\n' "$KEY" > /etc/arbitredge-v4/odds_api_key; chmod 600 /etc/arbitredge-v4/odds_api_key
runuser -u arbitredge-dev -- bash -c "echo 'leaked $KEY' > /srv/arbitredge_v4/logs/leak.txt"
out=$(bash "$PKG/verify_devhost.sh" 2>&1); rc=$?
expect "key leaked into a dev-readable file detected" test $rc -ne 0
expect "  ...verify output does not print the key" bash -c "! grep -q '$KEY' <<<\"\$1\"" _ "$out"
rm -f /srv/arbitredge_v4/logs/leak.txt
python3 - <<'PY'
import json; p='/etc/arbitredge-v4/gateway.json'; c=json.load(open(p)); c['live_enabled']=True; c['v4_monthly_cap']=20000; json.dump(c,open(p,'w'))
PY
out=$(bash "$PKG/verify_devhost.sh" 2>&1); rc=$?
expect "live enabled without owner flag / cap above 6000 detected" test $rc -ne 0
out=$(bash "$PKG/verify_devhost.sh" 2>&1 | grep -c FAIL)
expect "  ...both reported" test "$out" -ge 2
clean
echo; echo "RESULT: $pass passed, $fail failed"; (( fail == 0 ))
