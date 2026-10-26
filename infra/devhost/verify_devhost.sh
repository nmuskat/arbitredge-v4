#!/usr/bin/env bash
# =============================================================================
# ArbitrEdge V4 dev server — security verification (read-only; run as root).
# Every check prints PASS / FAIL / SKIP. Exit status 0 only if nothing FAILed.
# Run any time:  sudo bash verify_devhost.sh
# =============================================================================
set -uo pipefail
PROD_IP="${PROD_IP:-143.244.160.20}"
DEV_USER="${DEV_USER:-arbitredge-dev}"
GW_USER="${GW_USER:-arbitredge-gw}"
SOCK_GROUP="${SOCK_GROUP:-arbitredge-gwsock}"
V4_ROOT="${V4_ROOT:-/srv/arbitredge_v4}"
GW_CODE="${GW_CODE:-/opt/arbitredge-v4-gateway}"
ETC_DIR="${ETC_DIR:-/etc/arbitredge-v4}"
STATE_DIR="${STATE_DIR:-/var/lib/arbitredge-v4-gateway}"
SOCK="${SOCK:-/run/arbitredge-v4-gateway/gateway.sock}"
MANAGED_SETTINGS="${MANAGED_SETTINGS:-/etc/claude-code/managed-settings.json}"
CONTAINER_TEST="${CONTAINER_TEST:-0}"

pass=0; fail=0; skip=0
ok()   { printf '  PASS  %s\n' "$*"; pass=$((pass+1)); }
bad()  { printf '  FAIL  %s\n' "$*"; fail=$((fail+1)); }
skp()  { printf '  SKIP  %s\n' "$*"; skip=$((skip+1)); }
check() { local desc=$1; shift; if "$@" >/dev/null 2>&1; then ok "$desc"; else bad "$desc"; fi; }
check_not() { local desc=$1; shift; if "$@" >/dev/null 2>&1; then bad "$desc"; else ok "$desc"; fi; }
as_dev() { runuser -u "$DEV_USER" -- "$@"; }

[[ $EUID -eq 0 ]] || { echo "run as root"; exit 2; }

echo "== Not production"
check_not "host does not carry the production IP" bash -c "hostname -I | tr ' ' '\n' | grep -qx '$PROD_IP'"
check_not "/opt/arbitredge (V3) absent" test -e /opt/arbitredge

echo "== Dev user has no privilege"
check "dev user exists" id "$DEV_USER"
check_not "dev user not in sudo/admin/wheel/adm/root/$GW_USER" \
  bash -c "id -nG '$DEV_USER' | tr ' ' '\n' | grep -qxE 'sudo|admin|wheel|adm|root|$GW_USER'"
check "dev user is in $SOCK_GROUP (gateway socket only)" bash -c "id -nG '$DEV_USER' | tr ' ' '\n' | grep -qx '$SOCK_GROUP'"
check_not "no sudoers entry mentions the dev user" bash -c "grep -rqs '$DEV_USER' /etc/sudoers /etc/sudoers.d"
check "dev password is locked" bash -c "passwd -S '$DEV_USER' | awk '{print \$2}' | grep -qE '^L'"
check_not "dev user has no SSH private keys" bash -c "ls /home/$DEV_USER/.ssh/id_* 2>/dev/null | grep -q ."

echo "== Odds API key and gateway are out of the dev user's reach"
if [[ -f "$ETC_DIR/odds_api_key" ]]; then
  check "key file is root:root 0600" bash -c "[[ \$(stat -c '%U:%G %a' '$ETC_DIR/odds_api_key') == 'root:root 600' ]]"
  check_not "dev user cannot read the key file" as_dev test -r "$ETC_DIR/odds_api_key"
  # The key value must not appear anywhere the dev user can read.
  key_hits=$(python3 -I - "$ETC_DIR/odds_api_key" "$DEV_USER" "$GW_CODE" <<'PY'
import os, pwd, sys
sys.path.insert(0, sys.argv[3])
import odds_gateway
key = odds_gateway.read_key(sys.argv[1]).encode()
dev = pwd.getpwnam(sys.argv[2])
groups = set(os.getgrouplist(dev.pw_name, dev.pw_gid))
hits = 0
for root in ("/srv/arbitredge_v4", f"/home/{sys.argv[2]}", "/tmp", "/var/tmp", "/var/log"):
    for dp, dns, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                st = os.lstat(p)
                if not os.path.isfile(p) or os.path.islink(p) or st.st_size > 50_000_000:
                    continue
                readable = (st.st_mode & 0o004) or (st.st_uid == dev.pw_uid and st.st_mode & 0o400) \
                    or (st.st_gid in groups and st.st_mode & 0o040)
                if readable and key in open(p, "rb").read():
                    hits += 1
                    print("key found in dev-readable file: " + p, file=sys.stderr)
            except OSError:
                pass
print(hits)
PY
)
  [[ "$key_hits" == 0 ]] && ok "key value found in no dev-readable file" || bad "key value found in $key_hits dev-readable file(s)"
  if [[ "$CONTAINER_TEST" != 1 ]]; then
    jhits=$(journalctl --no-pager -q 2>/dev/null | python3 -I -c '
import sys; sys.path.insert(0, sys.argv[2]); import odds_gateway
k = odds_gateway.read_key(sys.argv[1]); print(sum(1 for l in sys.stdin if k in l))' "$ETC_DIR/odds_api_key" "$GW_CODE")
    [[ "$jhits" == 0 ]] && ok "key value absent from the system journal" || bad "key value appears $jhits time(s) in the journal"
  fi
else
  skp "key not installed yet (run transfer/send_odds_key.sh)"
fi
check_not "dev user cannot read gateway config" as_dev test -r "$ETC_DIR/gateway.json"
check_not "dev user cannot list gateway ledger dir" as_dev ls "$STATE_DIR"
check_not "dev user cannot write gateway code" as_dev test -w "$GW_CODE/odds_gateway.py"
check_not "dev user cannot write gateway code dir" as_dev test -w "$GW_CODE"
check "gateway code root-owned" bash -c "[[ \$(stat -c %U '$GW_CODE/odds_gateway.py' '$GW_CODE/credits.py' | sort -u) == root ]]"
check "admin tool root-only (0700)" bash -c "[[ \$(stat -c '%U %a' /usr/local/sbin/arbitredge-v4-gateway-admin) == 'root 700' ]]"
check "live Odds API calls disabled unless owner enabled them" python3 -I -c "
import json,sys; c=json.load(open('$ETC_DIR/gateway.json'))
sys.exit(0 if (c.get('live_enabled') is False or '${OWNER_ENABLED_LIVE:-0}'=='1') else 1)"
check "V4 monthly cap <= 6000 (owner-approved)" python3 -I -c "
import json,sys; sys.exit(0 if json.load(open('$ETC_DIR/gateway.json'))['v4_monthly_cap']<=${APPROVED_CAP:-6000} else 1)"

if [[ "$CONTAINER_TEST" != 1 ]]; then
  if systemctl is-active -q arbitredge-v4-gateway; then
    ok "gateway service active"
    check "gateway runs as $GW_USER" bash -c "[[ \$(systemctl show -p User --value arbitredge-v4-gateway) == '$GW_USER' ]]"
    check "gateway NoNewPrivileges + ProtectSystem=strict" bash -c \
      "systemctl show -p NoNewPrivileges -p ProtectSystem arbitredge-v4-gateway | tr '\n' ' ' | grep -q 'NoNewPrivileges=yes ProtectSystem=strict'"
    check "socket is 0660 group $SOCK_GROUP" bash -c "[[ \$(stat -c '%a %G' '$SOCK') == '660 $SOCK_GROUP' ]]"
    check "dev user can query gateway status" as_dev python3 -I -c "
import sys; sys.path.insert(0,'$V4_ROOT/repo'); from edge_v4.odds_client import OddsClient; OddsClient('$SOCK').status()"
  else
    skp "gateway service not running (starts after key install)"
  fi
fi

echo "== Filesystem layout"
check "$V4_ROOT is root:$DEV_USER 0750" bash -c "[[ \$(stat -c '%U:%G %a' '$V4_ROOT') == 'root:$DEV_USER 750' ]]"
check "import dir is root-owned (dev read-only)" bash -c "[[ \$(stat -c '%U:%G %a' '$V4_ROOT/import') == 'root:$DEV_USER 750' ]]"
check_not "dev user cannot create files in import/" as_dev touch "$V4_ROOT/import/.probe"
check "dev user can write repo/data/logs/backups" as_dev bash -c "for d in repo data logs backups; do test -w '$V4_ROOT/'\$d || exit 1; done"
check "managed Claude policy root-owned and valid JSON" bash -c \
  "[[ \$(stat -c %U '$MANAGED_SETTINGS') == root ]] && python3 -I -m json.tool '$MANAGED_SETTINGS'"
check_not "dev user cannot modify the managed policy" as_dev test -w "$MANAGED_SETTINGS"
check "managed policy disables bypass mode and denies sudo + key access" python3 -I -c "
import json,sys; p=json.load(open('$MANAGED_SETTINGS'))['permissions']
need={'Bash(sudo *)','Read(//etc/arbitredge-v4/**)','WebFetch(domain:api.the-odds-api.com)','Bash(*143.244.160.20*)'}
sys.exit(0 if p.get('disableBypassPermissionsMode')=='disable' and need<=set(p['deny']) else 1)"
check "CLAUDE.md rules file root-owned" bash -c "[[ \$(stat -c %U '$V4_ROOT/CLAUDE.md') == root ]]"

echo "== Network exposure"
if [[ "$CONTAINER_TEST" != 1 ]]; then
  check "UFW active" bash -c "ufw status | grep -q 'Status: active'"
  check "UFW allows only 22/tcp inbound" bash -c "[[ \$(ufw status | awk '/ALLOW/ {print \$1}' | sort -u | tr '\n' ' ') == '22/tcp ' ]]"
  check "only SSH listens on non-loopback addresses" bash -c \
    "ss -ltnH | awk '{print \$4}' | grep -vE '^(127\.|\[::1\]|::1)' | grep -vE ':22\$' | grep -q . && exit 1 || exit 0"
  check "SSH password login disabled" bash -c "sshd -T | grep -qx 'passwordauthentication no'"
  check "SSH root login key-only" bash -c "sshd -T | grep -qE '^permitrootlogin (without-password|prohibit-password)$'"
  check "fail2ban active" systemctl is-active -q fail2ban
  check "unattended security upgrades enabled" bash -c "grep -q 'Unattended-Upgrade \"1\"' /etc/apt/apt.conf.d/20auto-upgrades"
  check "PostgreSQL listens on localhost only" bash -c \
    "ss -ltnH '( sport = :5432 )' | awk '{print \$4}' | grep -vE '^(127\.0\.0\.1|\[::1\]):5432\$' | grep -q . && exit 1 || exit 0"
  check "dev user can use its database" as_dev psql -d arbitredge_v4 -qtAc "select 1"
  check "swap configured" bash -c "swapon --show --noheadings | grep -q ."
  check "nightly backup timer enabled" systemctl is-enabled -q arbitredge-v4-backup.timer
else
  skp "network, SSH, PostgreSQL, swap, timer checks (container test mode)"
fi

echo
echo "RESULT: $pass passed, $fail failed, $skip skipped"
(( fail == 0 ))
