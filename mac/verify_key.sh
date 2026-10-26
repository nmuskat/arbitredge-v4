#!/bin/bash
# ONE minimal live request: GET /v4/sports, which The Odds API documents as costing 0 credits.
# Shows whether the key works and your account's remaining credits. The key is not shown.
set -euo pipefail
source "$(dirname "$0")/config.sh"
GATEWAY_CONFIG="$GW_CONFIG" ODDS_KEY_KEYCHAIN_SERVICE="$KEYCHAIN_SERVICE" \
  "$V4_PY" -I "$V4_REPO/infra/devhost/gateway/odds_gateway.py" probe-local
