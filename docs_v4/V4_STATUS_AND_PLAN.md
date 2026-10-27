# ArbitrEdge V4: status audit and implementation plan (2026-10-27)

Every claim below was checked against the repository, the GitHub Actions run history, and the release-asset data.

## 1. What is genuinely operational

| Component | Evidence |
|---|---|
| Live Odds API collection (NFL + all FBS CFB; 10 books; spreads/totals/moneylines) | `odds-collect` runs on schedule since 2026-10-27 00:40 UTC. First fetch: 12 NFL + 62 CFB games, all 10 books including Pinnacle, 0 malformed records. |
| Credit gate and V3 protection | 500/month test cap (hard maximum 6,000), 60/day, 10 per request, V3 floor 2,000. Worst-case month test: 498 credits. Provider-reported cost = estimate (3/fetch). |
| Credit ledger and data persistence | Release assets (`v4-state`, `odds-data-YYYY-Www`). Spend is saved after every request; a lost ledger is refused. |
| Point-in-time market store, freshness, observed closes | `market_v4` (tests, outage replay) |
| Line-shopping scanner on real odds | `edge_v4/forward.py`, policy v4.1: market-maker consensus, method-robust vig removal, plausibility cap, longshot limit. 10 real WATCH candidates at first snapshot. |
| Closing-EV analysis | `analysis_v4/line_shop.py`; daily `odds-analyze` workflow (first run due 13:23 UTC). No closes observed yet, so INSUFFICIENT DATA. |
| Engine C (NFL) | Walk-forward 2012–2025: **ties the closing market, no edge**. Used for pricing (key numbers, pushes). |
| Validation framework | Planted-edge controls pass: finds a real edge, invents none, rejects an illusion. Every real-data backtest so far: NOT PROVEN. |
| Mobile staging page | Private Claude-hosted page (static snapshot, manual republish). |

## 2. Simulated, demo or labelled assumptions

| Item | Status |
|---|---|
| Synthetic sportsbook (Engine A) | Works (10k bettors, 3 markets, immutable ledger, exact Need). Behaviour parameters are **unvalidated assumptions**. On the site it still runs on **demo** quotes (Need page). |
| Bettor population per sport | Code supports `nfl` and `cfb` populations; only NFL has been run on the site. |
| Sharp-money score | Uncalibrated heuristic. Real data has 1 snapshot per game, so nearly everything is INSUFFICIENT EVIDENCE (correct). |
| Betting splits | Only synthetic splits exist; no real split source is licensed. |

## 3. Unfinished or blocked

| Item | Blocker | Who |
|---|---|---|
| CFB AP Top-25 filter | Needs a rankings source. The free CollegeFootballData API needs a free account and API key; no keyless legitimate source was found (cfbfastR schedules carry no ranks). | **Owner: create a free key** |
| CFB final scores for grading | Same CFBD key (avoids spending Odds API credits on `/scores`) | Owner (same key) |
| Automatic dashboard refresh | Claude-hosted pages cannot fetch from GitHub, and this account has no connectors. Needs either a free private host (Cloudflare Pages + Access) or an approved scheduled Claude task that republishes. | **Owner: choose** |
| Real betting splits, real RLM, ticket/handle divergence | Licensed data only (see §5) | Owner (cost decision) |
| Original ArbitrEdge SIMS | Source never provided; not substituted | Owner (provide repository) |
| CFB game model (SIMS-style) | Needs CFB history (scores + lines); CFBD key unlocks it | After key |
| NFL player props | Odds API event-level markets cost more credits; model not validated | Later, with approval |
| Parlays and correlated liabilities | The Odds API returns no parlay pricing, so modelling parlays would invent prices; deferred | — |

## 4. Prioritised plan (this round; each item tested and pushed)

1. **Automatic site pipeline** (no new cost, no new API calls). GitHub Actions rebuilds the full site from already-collected data after each collection and analysis run, and stores the built page as a release asset. Then:
   - Browser-side freshness labels (LIVE ≤ 45 min, DELAYED ≤ 6 h, STALE after that), so a page viewed later never shows old odds as current.
   - NFL / CFB-Top-25 switch, the requested sections, a detailed view per game, and filters.
   - Publishing target: owner's choice (§6).
2. **Synthetic sportsbook on real quotes for both sports.** Separate 10k NFL and 10k CFB populations, running on the collected odds. Ticket % from wager counts and handle % from stakes, per market and side. Need from liability. Sensitivity bands from parameter variants, labelled MODELED.
3. **Top-25 rankings module.** CFBD client, poll snapshots stored as release assets with source and timestamp, team-name matching with an unmatched-name report, game → applicable poll. Missing rankings mean UNAVAILABLE, never guessed.
4. **Sharp money.** Sport-specific thresholds (CFB moves larger and is less liquid). Per-book coverage and reliability measured from real data. RLM and ticket/handle divergence marked unavailable until real splits exist.
5. **Splits provider research** for an owner decision.
6. Later: CFB SIMS (after key), closing-EV results, then realized-ROI grading as games finish.

## 5. Approvals that will be requested

- CFBD free API key (credential).
- Publishing target for automatic refresh (free options).
- Any paid betting-splits feed.
- Any increase in Odds API usage.
