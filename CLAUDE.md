# ArbitrEdge V4: rules for every Claude Code session in this repository

The owner (nmuskat) has no coding background. You do the engineering, testing and debugging. Interrupt the owner only for spending, credentials, permissions, material scope changes, or production deployment.

## Hard rules

1. **V3 production is off-limits.** Never connect to 143.244.160.20, `/opt/arbitredge`, or its database, services or credentials. V3 code in this repository is reference only.
2. **The Odds API key:**
   - It lives only in the macOS Keychain (service `arbitredge-v4-odds-api`) and is read only by the gateway (`infra/devhost/gateway/odds_gateway.py`).
   - Never read, print, log, copy or commit it. Never call the Odds API directly.
   - All requests go through `edge_v4.odds_client.OddsClient` (socket `~/ArbitrEdgeV4/run/gateway.sock`).
   - On a single-user Mac these are policy guardrails, not OS isolation: follow them strictly.
3. **Credits** (shared 20,000/month subscription with V3):
   - The owner approved a V4 cap of 6,000/month, starting with a test budget of 500/month (150/day, 30 per request) set by `mac/bootstrap.sh`.
   - Raise caps only with `mac/set_budget.sh`, never above 6,000, and only after the test phase shows provider-reported costs matching estimates (no `cost mismatch` in `~/ArbitrEdgeV4/data/collector_log.jsonl`) and V3's floor is untouched.
   - Report every budget change to the owner. Never weaken the gateway's caps, V3 floor or breaker.
4. **No paid services or subscriptions.** No new hosting.
5. **Never fabricate data.** Missing quotes, closes or results are reported as unavailable. Demo/simulated data is always labelled.
6. **No official plays** until a strategy meets `docs_v4/V4_REBUILD_PLAN.md` §G. Default decision: NO BET.
7. **Tests:** never claim a test passed unless it ran; report exact counts. Run `.venv/bin/python -m pytest -q tests_v4 tests_edge tests_market tests_sharp tests_sims tests_web tests_collect` before every push.
8. **Commit** to branch `v4-dev` and push to `origin` (github.com/nmuskat/arbitredge-v4). Never force-push. Never commit anything from `~/ArbitrEdgeV4/`, `.venv/` or `data/`.

## Mac layout

| What | Where |
|---|---|
| Code | this repository |
| Python | `.venv/` (made by `mac/bootstrap.sh`) |
| Live market data | `~/ArbitrEdgeV4/data/market_live.db` (append-only quote store) |
| Collector state and log | `~/ArbitrEdgeV4/data/collector_state.json`, `collector_log.jsonl` |
| Gateway config, ledger, log | `~/ArbitrEdgeV4/gateway/`, `~/ArbitrEdgeV4/logs/gateway.log` |
| Background agents | `~/Library/LaunchAgents/com.arbitredge.v4.{gateway,collector}.plist` |

## Current priority: does line shopping produce positive EV on real odds?

1. Collect NFL and CFB odds: 10 books × spreads/totals/moneyline, 3 credits per fetch. The cadence speeds up near kickoff so every game's close is observed (`collect_v4/collector.py`).
2. Every day, run `python -m analysis_v4.line_shop --out docs_v4/line_shop_latest.json`:
   - opportunities are counted once, at first sighting;
   - the main test is **closing EV** (the price taken, evaluated at the observed closing consensus), with game-clustered confidence intervals;
   - break results down by book, market and hours to kickoff.
3. Grade results when final scores are available: NFL from nflverse (free); CFB only if credits allow (`/scores`).
4. Write findings to `docs_v4/DEVLOG.md` and summarise for the owner: sample size, confidence intervals, verdict. Be honest.
