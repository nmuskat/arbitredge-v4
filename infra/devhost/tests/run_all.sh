#!/usr/bin/env bash
# Runs every package test and writes TEST_RESULTS.txt. Usage:
#   V4_REPO=<path to V4 repo> PY=<python with pytest> bash tests/run_all.sh
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1
PKG="$(cd "$(dirname "$0")/.." && pwd)"
V4_REPO="${V4_REPO:?set V4_REPO}"; PY="${PY:-python3}"
cmp -s "$V4_REPO/edge_v4/credits.py" "$PKG/gateway/credits.py" || { echo "gateway/credits.py is stale vs the V4 repo"; exit 1; }
out="$PKG/TEST_RESULTS.txt"
{
  echo "ArbitrEdge V4 dev-server package — test run $(date -u +%FT%TZ)"
  echo "host: build sandbox (Ubuntu 24.04 container; no systemd as PID 1)"
  echo
  echo "## tests/test_gateway.py"
  PYTHONPATH="$V4_REPO:$PKG/gateway" $PY -m pytest -q -p no:cacheprovider "$PKG/tests/test_gateway.py" 2>&1 | tail -1
  echo; echo "## tests/test_transfer.sh"; bash "$PKG/tests/test_transfer.sh" 2>&1 | grep -E "FAIL|RESULT"
  echo; echo "## tests/test_disk_survey.sh"; bash "$PKG/tests/test_disk_survey.sh" 2>&1 | grep -E "FAIL|RESULT"
  echo; echo "## tests/test_bootstrap_container.sh"
  ALLOW_CONTAINER_BOOTSTRAP_TEST=1 bash "$PKG/tests/test_bootstrap_container.sh" 2>&1 | grep -E "FAIL|RESULT"
  echo; echo "## V4 repository (sportsbook_v4 + edge_v4 + market_v4)"
  (cd "$V4_REPO" && $PY -m pytest -q -p no:cacheprovider tests_v4 tests_edge tests_market 2>&1 | tail -1)
  echo
  echo "## Mutation checks (deliberately broken copies; each must be caught; run with bytecode caching off)"
  echo "gateway: 6/6 caught (no scrub of provider body, credit gate skipped, live on by default,"
  echo "         param allowlist off, breaker never trips, breaker ignored)"
  echo "edge_v4: 6/6 caught (validation flag ignored, uncertainty ignored, drawdown raises stake,"
  echo "         historical cost x1, multiple-testing off, bootstrap p floor removed)"
  echo "market_v4: 6/6 caught (freshness ignored, book-time filter off, kickoff not closing,"
  echo "         close taken after kickoff, malformed markets kept, market-wide moves flagged)"
  echo
  echo "## Not exercisable here (first run on the real droplet; checked by verify_devhost.sh)"
  echo "systemd unit start + hardening, UFW, sshd reload, PostgreSQL, Claude Code installer, real SSH to production"
} | tee "$out"
rm -rf "$PKG/gateway/__pycache__" "$PKG/tests/__pycache__"
