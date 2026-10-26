#!/bin/bash
# ArbitrEdge V4 -- one-time Mac setup (no admin password, no paid software).
# Installs: uv (Python manager, in ~/.local/bin), Python 3.12 + V4 packages in
# the repo's .venv, ~/ArbitrEdgeV4 data folders, and the Odds API gateway as a
# background agent. Live API calls stay OFF. Collection is NOT started here.
set -euo pipefail
source "$(dirname "$0")/config.sh"
[[ "$(uname -s)" == Darwin ]] || { echo "This script is for macOS."; exit 1; }

echo "== Python environment"
if ! command -v uv >/dev/null 2>&1 && [[ ! -x "$HOME/.local/bin/uv" ]]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
"$UV" venv -q -p 3.12 "$V4_REPO/.venv"
"$UV" pip install -q -p "$V4_PY" -r "$V4_REPO/requirements-v4.txt"
"$V4_PY" -c "import numpy, pandas, fastapi; print('python packages ok')"

echo "== Folders (private to your user)"
mkdir -p "$V4_HOME"/{run,data,gateway/state,logs}
chmod 700 "$V4_HOME" "$V4_HOME"/{run,data,gateway,gateway/state,logs}

echo "== Gateway settings (initial TEST budget; live calls off)"
if [[ ! -f "$GW_CONFIG" ]]; then
  cat > "$GW_CONFIG" <<JSON
{
  "base_url": "https://api.the-odds-api.com",
  "socket_path": "$V4_HOME/run/gateway.sock",
  "socket_group": null,
  "state_dir": "$V4_HOME/gateway/state",
  "live_enabled": false,
  "allowed_sports": ["americanfootball_nfl", "americanfootball_ncaaf"],
  "v4_monthly_cap": 500,
  "v4_daily_cap": 150,
  "v3_floor_remaining": 2000,
  "max_single_request": 30,
  "max_cache_ttl_s": 86400,
  "rate_limit_cooldown_s": 60,
  "upstream_timeout_s": 20
}
JSON
  chmod 600 "$GW_CONFIG"
fi

echo "== Gateway background agent"
mkdir -p "$AGENTS"
cat > "$AGENTS/$GW_LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$GW_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$V4_PY</string><string>-I</string><string>$V4_REPO/infra/devhost/gateway/odds_gateway.py</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>GATEWAY_CONFIG</key><string>$GW_CONFIG</string>
    <key>ODDS_KEY_KEYCHAIN_SERVICE</key><string>$KEYCHAIN_SERVICE</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>$V4_HOME/logs/gateway.log</string>
  <key>StandardOutPath</key><string>$V4_HOME/logs/gateway.log</string>
</dict></plist>
PLIST
launchctl bootout "gui/$(id -u)/$GW_LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AGENTS/$GW_LABEL.plist"
sleep 2
[[ -S "$V4_HOME/run/gateway.sock" ]] && echo "gateway running" || { echo "gateway did not start: see $V4_HOME/logs/gateway.log"; exit 1; }
echo "Setup complete. Next: add the Odds API key with mac/add_api_key.sh"
