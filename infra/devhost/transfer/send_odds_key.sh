#!/usr/bin/env bash
# =============================================================================
# Install the Odds API key on the V4 dev droplet WITHOUT displaying it.
#
# Run ON THE DEV DROPLET as root, in a session opened with agent forwarding
# from your computer:      ssh -A root@<dev droplet IP>
#                          bash transfer/send_odds_key.sh
#
# Production access is READ-ONLY: one grep of the env file for the single
# THE_ODDS_API_KEY line. Nothing on production is written or restarted.
# The value travels inside SSH, is held in a shell variable (never in argv,
# never echoed, never in history), and is written to a root:root 0600 file
# that only systemd hands to the gateway service.
# =============================================================================
set -Eeuo pipefail
umask 077
PROD="${PROD:-root@143.244.160.20}"
PROD_SSH="${PROD_SSH:-ssh -T -o ConnectTimeout=15}"
ETC_DIR="${ETC_DIR:-/etc/arbitredge-v4}"
GW_CODE="${GW_CODE:-/opt/arbitredge-v4-gateway}"
RESTART="${RESTART:-1}"
set +o history 2>/dev/null || true
[[ $EUID -eq 0 ]] || { echo "run as root on the dev droplet" >&2; exit 2; }
[[ -e /opt/arbitredge ]] && { echo "refusing: this looks like production (/opt/arbitredge exists)" >&2; exit 2; }
[[ -d "$ETC_DIR" ]] || { echo "run devhost_bootstrap.sh first ($ETC_DIR missing)" >&2; exit 2; }

# Read-only locator, executed on production. Prints ONLY the key line on stdout;
# diagnostics (file path, never the value) go to stderr.
read -r -d '' FIND <<'REMOTE' || true
set -u
cands=""
[ -n "${PROD_ENV_FILE:-}" ] && cands="$PROD_ENV_FILE"
for u in $(systemctl list-units --type=service --all --no-legend 'arbitredge*' 'gunicorn*' 2>/dev/null | awk '{print $1}'); do
  for f in $(systemctl show -p EnvironmentFiles --value "$u" 2>/dev/null | awk '{print $1}' | sed 's/^-//'); do
    cands="$cands $f"
  done
done
cands="$cands /opt/arbitredge/.env"
for f in $cands; do
  if [ -f "$f" ] && grep -qE '^[[:space:]]*(export[[:space:]]+)?THE_ODDS_API_KEY=' "$f"; then
    echo "found THE_ODDS_API_KEY in $f" >&2
    grep -m1 -E '^[[:space:]]*(export[[:space:]]+)?THE_ODDS_API_KEY=' "$f"
    exit 0
  fi
done
echo "THE_ODDS_API_KEY not found in: $cands" >&2
exit 1
REMOTE

echo "Reading the key line from production (read-only)..."
KEYLINE="$($PROD_SSH "$PROD" "PROD_ENV_FILE='${PROD_ENV_FILE:-}' sh -s" <<<"$FIND")" || {
  echo "could not read the key from production; nothing installed" >&2; exit 1; }

tmp="$ETC_DIR/odds_api_key.new"
rm -f "$tmp"
printf '%s\n' "$KEYLINE" > "$tmp"          # printf is a shell builtin: the value never appears in a process list
unset KEYLINE
chown root:root "$tmp"; chmod 600 "$tmp"
if ! len=$(python3 -I -c 'import sys; sys.path.insert(0, sys.argv[2]); import odds_gateway as g; print(len(g.read_key(sys.argv[1])))' "$tmp" "$GW_CODE" 2>/dev/null); then
  rm -f "$tmp"; echo "value read from production does not look like an Odds API key; nothing installed" >&2; exit 1
fi
mv -f "$tmp" "$ETC_DIR/odds_api_key"
echo "Key installed: $ETC_DIR/odds_api_key (root:root 0600, ${len} characters). Value not displayed."
if [[ "$RESTART" == 1 ]]; then
  systemctl restart arbitredge-v4-gateway
  sleep 1
  systemctl is-active -q arbitredge-v4-gateway && echo "Gateway restarted with the key. Live calls remain DISABLED." \
    || { echo "gateway failed to start: journalctl -u arbitredge-v4-gateway" >&2; exit 1; }
  echo "Optional zero-credit check (one /v4/sports call): arbitredge-v4-gateway-admin test-key"
fi
