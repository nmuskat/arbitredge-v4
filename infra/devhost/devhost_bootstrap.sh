#!/usr/bin/env bash
# =============================================================================
# ArbitrEdge V4 — development server bootstrap
#
# RUN ONLY ON THE NEW, DEDICATED V4 DEVELOPMENT DROPLET. NEVER ON PRODUCTION.
# The script refuses to run on anything that looks like the V3 production
# server (IP 143.244.160.20, /opt/arbitredge present, arbitredge services).
#
#   sudo bash devhost_bootstrap.sh --check     # preflight only, changes nothing
#   sudo bash devhost_bootstrap.sh             # preflight, show plan, ask, apply
#
# What it sets up (all on this droplet only):
#   * users:  arbitredge-dev  (Claude Code + V4 code; NO sudo, password locked)
#             arbitredge-gw   (system user that runs the Odds API gateway)
#     group:  arbitredge-gwsock (lets arbitredge-dev talk to the gateway socket)
#   * Odds API gateway (systemd, hardened). The API key file is root:root 0600
#     and is handed to the gateway alone via systemd LoadCredential. The dev
#     user can never read it. Live calls stay OFF until the owner enables them.
#   * firewall (UFW: only SSH inbound), SSH key-only, fail2ban,
#     unattended security upgrades, 4 GB swap, UTC clock
#   * PostgreSQL 16 (localhost only), role + database for arbitredge-dev
#   * Python venv, V4 repo (from the handoff bundle), Claude Code (official
#     installer, as arbitredge-dev) with a root-owned managed policy
#   * nightly backup timer (pg_dump + git bundle, keep 7) as arbitredge-dev
#
# Idempotent: re-running converges; existing owner-edited config is kept.
# =============================================================================
set -Eeuo pipefail
umask 022

PROD_IP="${PROD_IP:-143.244.160.20}"
DEV_USER="${DEV_USER:-arbitredge-dev}"
GW_USER="${GW_USER:-arbitredge-gw}"
SOCK_GROUP="${SOCK_GROUP:-arbitredge-gwsock}"
V4_ROOT="${V4_ROOT:-/srv/arbitredge_v4}"
GW_CODE="${GW_CODE:-/opt/arbitredge-v4-gateway}"
ETC_DIR="${ETC_DIR:-/etc/arbitredge-v4}"
MIN_RAM_MB="${MIN_RAM_MB:-7000}"
MIN_DISK_GB="${MIN_DISK_GB:-100}"
SWAP_GB="${SWAP_GB:-4}"
PKG_DIR="${PKG_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
BUNDLE="${BUNDLE:-$PKG_DIR/arbitredge_v4.bundle}"
# Test hook only: skip steps a container cannot perform (systemd, ufw, sshd, apt).
MANAGED_SETTINGS="${MANAGED_SETTINGS:-/etc/claude-code/managed-settings.json}"
CONTAINER_TEST="${CONTAINER_TEST:-0}"
ASSUME_YES="${ASSUME_YES:-0}"

MODE=apply
case "${1:-}" in
  --check) MODE=check ;;
  "") ;;
  *) echo "usage: $0 [--check]" >&2; exit 64 ;;
esac

say()  { printf '%s\n' "$*"; }
hdr()  { printf '\n== %s\n' "$*"; }
REFUSALS=()
refuse() { REFUSALS+=("$*"); }

# ----------------------------------------------------------------------------
hdr "Preflight (read-only)"
[[ $EUID -eq 0 ]] || { echo "must run as root (sudo)"; exit 2; }

# --- production guard: any single hit is a hard refusal --------------------
public_ips="$(hostname -I 2>/dev/null || true)"
if [[ " $public_ips " == *" $PROD_IP "* ]]; then
  refuse "this host has the PRODUCTION IP $PROD_IP. This script must never run on production."
fi
if [[ "$CONTAINER_TEST" != 1 ]] && command -v curl >/dev/null; then
  md_ip="$(curl -s -m 2 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null || true)"
  [[ "$md_ip" == "$PROD_IP" ]] && refuse "DigitalOcean metadata reports the PRODUCTION IP $PROD_IP."
fi
[[ -e /opt/arbitredge ]] && refuse "/opt/arbitredge exists: this looks like the V3 production server."
if command -v systemctl >/dev/null && [[ "$CONTAINER_TEST" != 1 ]]; then
  if systemctl list-unit-files --no-legend 2>/dev/null | awk '{print $1}' | grep -E '^(arbitredge|gunicorn|caddy)' \
       | grep -v '^arbitredge-v4-' >/dev/null; then
    refuse "V3-style services (arbitredge*/gunicorn*/caddy*) are installed: this looks like production."
  fi
fi

# --- platform and capacity --------------------------------------------------
. /etc/os-release 2>/dev/null || true
[[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] || refuse "expected Ubuntu 24.04 LTS (found ${PRETTY_NAME:-unknown})."
ram_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
(( ram_mb >= MIN_RAM_MB )) || refuse "RAM ${ram_mb} MB < ${MIN_RAM_MB} MB (the approved plan is an 8 GB droplet)."
disk_free_gb=$(df -P -BG / | awk 'NR==2 {gsub("G","",$4); print $4}')
(( disk_free_gb >= MIN_DISK_GB )) || refuse "free disk ${disk_free_gb} GB < ${MIN_DISK_GB} GB."

# --- SSH: we will disable password login; make sure that cannot lock the owner out
if [[ "$CONTAINER_TEST" != 1 ]]; then
  if [[ ! -s /root/.ssh/authorized_keys ]]; then
    refuse "root has no SSH authorized_keys. Create the droplet with SSH-key authentication first (password login will be disabled)."
  fi
fi

# --- pre-existing accounts must be ours -------------------------------------
if id "$DEV_USER" >/dev/null 2>&1 && [[ ! -e "/home/$DEV_USER/.arbitredge_v4_dev_user" ]]; then
  refuse "user $DEV_USER already exists and was not created by this script."
fi
if id "$DEV_USER" >/dev/null 2>&1 && id -nG "$DEV_USER" | tr ' ' '\n' | grep -qxE 'sudo|admin|wheel|root|adm'; then
  refuse "user $DEV_USER is in a privileged group."
fi

[[ -f "$PKG_DIR/gateway/odds_gateway.py" && -f "$PKG_DIR/gateway/credits.py" ]] \
  || refuse "package incomplete: gateway/ files missing next to this script."
[[ -f "$BUNDLE" ]] || say "note: no repo bundle at $BUNDLE; repo will be created empty (clone from GitHub later)."

say "host: $(hostname)  ips: ${public_ips:-?}"
say "RAM: ${ram_mb} MB   free disk: ${disk_free_gb} GB   OS: ${PRETTY_NAME:-?}"

if ((${#REFUSALS[@]})); then
  hdr "REFUSED — nothing was changed"
  for r in "${REFUSALS[@]}"; do say "  ✗ $r"; done
  exit 2
fi
say "preflight: all checks passed"

if [[ $MODE == check ]]; then
  say "(--check: no changes made)"
  exit 0
fi

hdr "Plan"
cat <<EOF
  1. apt: security updates + python3-venv build-essential git sqlite3 tmux ufw
     fail2ban unattended-upgrades postgresql zstd jq acl
  2. ${SWAP_GB} GB swap file, UTC clock, unattended security upgrades
  3. SSH key-only (PasswordAuthentication no), UFW allow 22/tcp only, fail2ban
  4. users: $DEV_USER (no sudo, password locked), $GW_USER (system), group $SOCK_GROUP
  5. dirs: $V4_ROOT/{repo,data,logs,backups,venv}  (owned by $DEV_USER)
           $V4_ROOT/import                        (root-owned, read-only to $DEV_USER)
  6. gateway: $GW_CODE (root-owned code), $ETC_DIR/gateway.json (live_enabled=false)
  7. PostgreSQL: role "$DEV_USER", database arbitredge_v4, localhost only
  8. venv + V4 repo + Claude Code for $DEV_USER, managed policy in /etc/claude-code
  9. nightly backup timer; then run verify_devhost.sh
EOF
if [[ "$ASSUME_YES" != 1 ]]; then
  read -r -p "Apply these changes to THIS droplet? Type 'yes': " ans
  [[ "$ans" == yes ]] || { say "aborted; nothing changed"; exit 1; }
fi

run_as_dev() { runuser -u "$DEV_USER" -- "$@"; }

# ----------------------------------------------------------------------------
if [[ "$CONTAINER_TEST" != 1 ]]; then
  hdr "1. Packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get -y -q upgrade
  apt-get -y -q install python3-venv python3-dev build-essential git sqlite3 tmux ufw fail2ban \
    unattended-upgrades postgresql postgresql-contrib zstd jq acl curl ca-certificates

  hdr "2. Swap, clock, automatic security updates"
  if ! swapon --show=NAME --noheadings | grep -q .; then
    fallocate -l "${SWAP_GB}G" /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
  timedatectl set-timezone UTC || true
  printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
    > /etc/apt/apt.conf.d/20auto-upgrades

  hdr "3. SSH hardening, firewall, fail2ban"
  cat > /etc/ssh/sshd_config.d/10-arbitredge-v4.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
X11Forwarding no
MaxAuthTries 4
EOF
  sshd -t                                    # refuse to reload a broken config
  systemctl reload ssh || systemctl reload sshd
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw allow 22/tcp >/dev/null
  ufw --force enable >/dev/null
  systemctl enable --now fail2ban >/dev/null
fi

# ----------------------------------------------------------------------------
hdr "4. Users and groups"
getent group "$SOCK_GROUP" >/dev/null || groupadd --system "$SOCK_GROUP"
id "$GW_USER" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent \
  --shell /usr/sbin/nologin "$GW_USER"
if ! id "$DEV_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$DEV_USER"
  touch "/home/$DEV_USER/.arbitredge_v4_dev_user"
  chown "$DEV_USER:" "/home/$DEV_USER/.arbitredge_v4_dev_user"
fi
passwd -l "$DEV_USER" >/dev/null 2>&1 || true
usermod -a -G "$SOCK_GROUP" "$DEV_USER"
chmod 0750 "/home/$DEV_USER"
[[ "$CONTAINER_TEST" == 1 ]] || loginctl enable-linger "$DEV_USER"   # lets the dev user run its own user services

hdr "5. Directories"
safe_mkdir() {  # path owner group mode — refuses to follow symlinks
  local p=$1
  [[ -L "$p" ]] && { echo "refusing: $p is a symlink"; exit 3; }
  mkdir -p "$p"
  chown -h "$2:$3" "$p"; chmod "$4" "$p"
}
safe_mkdir "$V4_ROOT" root "$DEV_USER" 0750
for d in repo data logs backups; do safe_mkdir "$V4_ROOT/$d" "$DEV_USER" "$DEV_USER" 0750; done
safe_mkdir "$V4_ROOT/import" root "$DEV_USER" 0750       # production copy lands here (root writes, dev reads)

hdr "6. Odds API gateway"
safe_mkdir "$GW_CODE" root root 0755
install -o root -g root -m 0644 "$PKG_DIR/gateway/odds_gateway.py" "$GW_CODE/odds_gateway.py"
install -o root -g root -m 0644 "$PKG_DIR/gateway/credits.py" "$GW_CODE/credits.py"
safe_mkdir "$ETC_DIR" root root 0755
if [[ ! -f "$ETC_DIR/gateway.json" ]]; then             # keep owner edits on re-run
  install -o root -g "$GW_USER" -m 0640 "$PKG_DIR/gateway/gateway.json" "$ETC_DIR/gateway.json"
fi
safe_mkdir /var/lib/arbitredge-v4-gateway "$GW_USER" "$GW_USER" 0700   # ledger: gateway only
install -o root -g root -m 0700 "$PKG_DIR/gateway/arbitredge-v4-gateway-admin" /usr/local/sbin/arbitredge-v4-gateway-admin
if [[ "$CONTAINER_TEST" != 1 ]]; then
  install -o root -g root -m 0644 "$PKG_DIR/gateway/arbitredge-v4-gateway.service" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable arbitredge-v4-gateway >/dev/null
  if [[ -f "$ETC_DIR/odds_api_key" ]]; then
    systemctl restart arbitredge-v4-gateway
  else
    say "gateway enabled; it starts once the key is installed (laptop/send_odds_key.sh)."
  fi
fi

if [[ "$CONTAINER_TEST" != 1 ]]; then
  hdr "7. PostgreSQL (localhost only)"
  systemctl enable --now postgresql >/dev/null
  runuser -u postgres -- psql -qtAc "SELECT 1 FROM pg_roles WHERE rolname='$DEV_USER'" | grep -q 1 \
    || runuser -u postgres -- psql -qc "CREATE ROLE \"$DEV_USER\" LOGIN CREATEDB"
  runuser -u postgres -- psql -qtAc "SELECT 1 FROM pg_database WHERE datname='arbitredge_v4'" | grep -q 1 \
    || runuser -u postgres -- createdb -O "$DEV_USER" arbitredge_v4
fi

hdr "8. Python venv, repository, Claude Code"
if [[ ! -x "$V4_ROOT/venv/bin/python" ]]; then
  safe_mkdir "$V4_ROOT/venv" "$DEV_USER" "$DEV_USER" 0750
  run_as_dev python3 -m venv "$V4_ROOT/venv"
fi
if [[ "$CONTAINER_TEST" != 1 ]]; then
  run_as_dev "$V4_ROOT/venv/bin/pip" install -q --upgrade pip
  run_as_dev "$V4_ROOT/venv/bin/pip" install -q -r "$PKG_DIR/requirements-dev.txt"
  run_as_dev bash -c "$V4_ROOT/venv/bin/pip freeze > $V4_ROOT/logs/requirements.lock.txt"
fi
if [[ -f "$BUNDLE" && ! -d "$V4_ROOT/repo/.git" ]]; then
  install -o root -g "$DEV_USER" -m 0640 "$BUNDLE" "$V4_ROOT/import/arbitredge_v4.bundle"
  run_as_dev git clone -q -b v4-dev "$V4_ROOT/import/arbitredge_v4.bundle" "$V4_ROOT/repo"
  run_as_dev git -C "$V4_ROOT/repo" remote rename origin handoff-bundle
fi

mkdir -p "$(dirname "$MANAGED_SETTINGS")"
install -o root -g root -m 0644 "$PKG_DIR/claude-managed-settings.json" "$MANAGED_SETTINGS"
install -o root -g root -m 0644 "$PKG_DIR/CLAUDE_DEVHOST.md" "$V4_ROOT/CLAUDE.md"
if [[ "$CONTAINER_TEST" != 1 && ! -x "/home/$DEV_USER/.local/bin/claude" ]]; then
  run_as_dev bash -c 'cd ~ && curl -fsSL https://claude.ai/install.sh | bash'
fi

if [[ "$CONTAINER_TEST" != 1 ]]; then
  hdr "9. Nightly backups"
  install -o root -g root -m 0755 "$PKG_DIR/backup/arbitredge-v4-backup.sh" /usr/local/bin/arbitredge-v4-backup
  install -o root -g root -m 0644 "$PKG_DIR/backup/arbitredge-v4-backup.service" /etc/systemd/system/
  install -o root -g root -m 0644 "$PKG_DIR/backup/arbitredge-v4-backup.timer" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now arbitredge-v4-backup.timer >/dev/null
fi

hdr "Verification"
bash "$PKG_DIR/verify_devhost.sh"

hdr "Done. Next (owner), in this same root session opened with:  ssh -A root@<this droplet IP>"
cat <<EOF
    1. bash $PKG_DIR/transfer/send_odds_key.sh     # key: production -> gateway, never displayed
    2. bash $PKG_DIR/transfer/copy_prod_db.sh      # newest production backup -> $V4_ROOT/import
    3. sudo -iu $DEV_USER
       ~/.local/bin/claude          # /login, then /exit
       tmux new -s v4 -c $V4_ROOT/repo "~/.local/bin/claude remote-control --name 'ArbitrEdge V4 dev'"
  Live Odds API calls stay OFF until you run:  arbitredge-v4-gateway-admin enable-live
EOF
