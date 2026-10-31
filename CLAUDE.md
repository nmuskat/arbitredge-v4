# ArbitrEdge V4: rules for every Claude Code session in this repository

The owner (nmuskat) has no coding background. You do the engineering, testing and debugging. Interrupt the owner only for spending, credentials, permissions, material scope changes, or production deployment.

## Hard rules

1. **V3 production is off-limits.** Never connect to 143.244.160.20, `/opt/arbitredge`, or its database, services or credentials. V3 code in this repository is reference only.
2. **The Odds API key** lives only in the GitHub Actions secret `ODDS_API_KEY`. The Mac/Keychain path is retired: the owner's Mac cannot run macOS 13.
   - Only `.github/workflows/odds-collect.yml` and `odds-verify.yml` may reference it, through `collect_v4/actions_run.py` and the credit gate.
   - Never read, print, log or commit it. Never call the Odds API outside the gate.
   - Never add a workflow, step or trigger that exposes it (pull_request triggers, third-party actions, debug printing).
   - **Never start `odds-verify` or `odds-collect` yourself, and never change the `V4_LIVE` variable.** Live activation is the owner's decision.
2b. **CollegeFootballData key** (secret `CFBD_API_KEY`, free tier): only `.github/workflows/cfb-rankings.yml` may use it, through `data_v4/rankings_run.py` (AP poll) and `data_v4/results.py` (CFB final scores for the Need tracker, owner-approved 2026-10-29, 1–2 calls per run). It is switched on by the owner's variable `CFBD_ENABLED`. Never print it, never change that variable yourself.
2c. **Cloudflare Pages token** (secrets `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`; scope: Account → Cloudflare Pages → Edit): used only by the deploy step of `.github/workflows/site-build.yml` through `.github/scripts/pages_deploy.sh`. Callers pass exactly these two secrets, never `secrets: inherit`. Deploys are switched on by the owner's variable `CF_PAGES_DEPLOY`; never change it yourself. Never weaken the Access check that refuses to deploy unless `<project>.pages.dev` and `*.<project>.pages.dev` both require Cloudflare Access login. See `docs_v4/CLOUDFLARE_PAGES.md`.
3. **Credits** (shared 20,000/month subscription with V3):
   - The owner approved a V4 cap of 6,000/month, starting with a test budget of 500/month (60/day, 10 per request).
   - The caps come from repository variables (`V4_MONTHLY_CAP`, default 500; the code hard-limits it to 6,000). Recommend raising it only after the test phase shows provider-reported costs matching estimates and V3's floor untouched. The owner changes the variable.
   - Report every budget change to the owner. Never weaken the gateway's caps, V3 floor or breaker.
4. **No paid services or subscriptions.** No new hosting.
5. **Never fabricate data.** Missing quotes, closes or results are reported as unavailable. Demo/simulated data is always labelled.
6. **No official plays** until a strategy meets `docs_v4/V4_REBUILD_PLAN.md` §G. Default decision: NO BET.
7. **Tests:** never claim a test passed unless it ran; report exact counts. Run `.venv/bin/python -m pytest -q tests_v4 tests_edge tests_market tests_sharp tests_sims tests_web tests_collect` before every push.
8. **Commit** to branch `v4-dev` and push to `origin` (github.com/nmuskat/arbitredge-v4). Never force-push. Never commit anything from `~/ArbitrEdgeV4/`, `.venv/` or `data/`.

## Live collection layout (GitHub Actions; see docs_v4/GITHUB_ACTIONS_PLAN.md)

| What | Where |
|---|---|
| Collection | `.github/workflows/odds-collect.yml` → `collect_v4/actions_run.py` (schedule in `collect_v4/cron_plan.py`, policy in `collect_v4/schedule_policy.py`) |
| Odds data | release assets `odds-data-YYYY-Www/quotes_*.jsonl.gz` (one per run, never modified) |
| Credit ledger and state | release `v4-state`: `ledger.jsonl`, `collector_state.json` |
| Daily analysis | `.github/workflows/odds-analyze.yml` → `analysis_v4/actions_analyze.py` → `v4-state/line_shop_latest.json` |
| Dashboard | `.github/workflows/site-build.yml` (after runs that saved new odds, paced by `schedule_policy.site_due`: 3 h quiet, 45 min in the final 6 h, always after closes, monthly limit 300 (each build bills 2 min)) → release `v4-site` + owner-only Cloudflare Pages |
| Book Need Tracker (MODELED, research only) | `sportsbook_v4/need_tracker.py`, run by site-build: pre-kickoff snapshots `v4-need-YYYY-Www/need_*.json.gz`, sealed decisions `v4-need-decisions/decisions_*.json.gz`, final scores `v4-results/results_*.json.gz` (NFL from `odds-analyze`, CFB from `cfb-rankings`). All immutable; never delete or overwrite |
| Official ArbitrEdge Needs (EXPERIMENTAL MODEL SIGNALS; strategies `official_needs.spread.v3` and `official_needs.total.v2`, earlier records `official_needs.v1`, `spread.v2`, `total.v1`) | `official_v5/strategies.py` and `official_v5/rules.py`, run by site-build (sealing also in the collection job): frozen pre-kickoff records `v4-need-YYYY-Www/official_snap_*.json.gz`, sealed selections `v4-need-decisions/official_dec_*.json.gz`. Immutable; separate from the Need tracker and C1-C4; see `docs_v5/OFFICIAL_NEEDS.md` |
| Synthetic-book ledger (MODELED) | release `v4-book`: `book_<sport>_<week>_<run>_<seq>.json.gz`, one immutable file per advance of a weekly run; never delete or overwrite (`sportsbook_v4/persist.py`) |

## Current priority: does line shopping produce positive EV on real odds?

1. Collect NFL and CFB odds: 10 books × spreads/totals/moneyline, 3 credits per fetch. The cadence speeds up near kickoff so every game's close is observed (`collect_v4/collector.py`).
   - Owner-approved 2026-10-29: **balanced** tier (about 757 credits/month); CFB closes target AP Top-25 kickoffs only; Sunday-morning window for international NFL games.
   - The tier and caps are the owner's repository variables `V4_TIER`, `V4_MONTHLY_CAP` and `V4_DAILY_CAP`. Never change them yourself.
   - Expected versus actual spend: `tools_v4/consumption_check.py`.
   - An **expanded** tier (about 3,300–3,700 credits/month) is built and tested but NOT approved. Never activate it; see `docs_v4/EXPANDED_TIER_PLAN.md`. GitHub Actions minutes, not credits, are its binding constraint.
   - Every paid request is preceded by a free `/v4/sports` balance check and fails closed. Never remove it.
2. The daily `odds-analyze` workflow runs the line-shopping report:
   - opportunities are counted once, at first sighting;
   - the main test is **closing EV** (the price taken, evaluated at the observed closing consensus), with game-clustered confidence intervals;
   - break results down by book, market and hours to kickoff.
3. Grade results when final scores are available: NFL from nflverse (free); CFB only if credits allow (`/scores`).
4. Write findings to `docs_v4/DEVLOG.md` and summarise for the owner: sample size, confidence intervals, verdict. Be honest.
