# ArbitrEdge V4 development log

Newest first. Test counts are from actual runs.

## 2026-10-26 (later): Engines A–C, decision pipeline and website built without a server

- **Data:** nflverse games 1999–2026 and play-by-play 2006–2026 (381 MB), with SHA-256 manifest. CFBD is blocked from the sandbox, so CFB is pending.
- **Engine C:** point-in-time Elo + EPA features, ridge margin/total models, key-number distributions calibrated by iterative proportional fitting (out-of-sample pushes 90 predicted vs 93 actual).
  - Walk-forward 2012–2025: the independent model is significantly worse than the market; the anchored model ties it.
  - **Conclusion: no model edge vs near-close NFL lines.**
- **Engine B:** price-only sharp evidence with an experimental score. Splits-based signals are reported as unavailable. The synthetic sportsbook is rejected as a source; demo data only when explicitly opted in and labelled.
- **Engine A m3:** totals and moneyline markets; Need for each market and the joint book (exact over every score 0–70). The market_v4 adapter enforces freshness, so an outage closes the book. A 10,000-bettor week runs in under 2 minutes.
- **Decision pipeline:**
  - Fair prices at any number, an append-only strategy registry and paper ledger.
  - Real-data backtests: all NOT PROVEN.
  - Synthetic controls: a planted edge is found, none is invented where absent, and the illusion is rejected.
  - Forward EV scanner.
- **Website:** 8 sections, provenance badges, read-only. Browser-tested at 1366 px and 390 px (no overflow, no console errors).
- **Bugs found by tests and fixed:**
  - `INSERT OR IGNORE` silently dropped CHECK/NOT NULL violations (ledger, quote store).
  - A recreational first mover was mislabelled as sharp.
  - Same-day kickoff leakage risk (guarded and tested).
  - Wrong design of the "noisy consensus" control (corrected).
- **Tests:**
  - 190 V4 tests pass locally with real data (sportsbook 73, edge 53, market 24, sharp 14, sims 11, web 15).
  - Clean Python 3.12 clone (CI simulation): 174 passed, 16 skipped (data-dependent).
  - Every planted regression in the new modules was caught.

### Milestone that genuinely needs infrastructure

**Real-data validation of line shopping (S1) and calibration of Engine B.** Both need timestamped multi-book odds with observed closes. Neither games.csv nor anything reachable from this sandbox has them.

The cheapest options, in order:
1. **Read-only copy of the V3 production backup:** $0 new spend, but it needs somewhere to put an 8.6 GB file that a session can reach.
2. **Targeted Odds API historical snapshots under the 6,000-credit V4 cap:** $0 new spend, but needs a machine that can reach the Odds API (blocked from this sandbox).
3. **The previously proposed droplet** (about $58/month). Not needed for anything else yet.

**Blocked on owner:** GitHub repository (code preservation), choice of data-access path above, SIMS code.

## 2026-10-26: P0 decision core; P2 market layer; dev-server package

- **edge_v4:** vig removal, EV with pushes, uncertainty-gated BET/WATCH/NO BET, fractional Kelly with caps, Odds API credit gate, validation statistics. 42 tests.
  - Mutation testing found a real defect: a bootstrap p-value of 0 nullified the multiple-testing correction. Fixed with (k+1)/(n+1).
  - The month boundary is now exact UTC (the provider resets credits on the 1st).
- **edge_v4.odds_client:** the only path from V4 to the Odds API, via the local gateway. V4 never holds the key.
- **market_v4:**
  - Odds API payload normaliser with provenance; malformed markets go to an anomaly log, never into the data.
  - Append-only quote store.
  - Point-in-time market state (OK / STALE / NO_DATA / CLOSED_KICKOFF / INCONSISTENT) with sharp-weighted no-vig consensus. The weights are an unvalidated assumption.
  - Observed closes are never imputed.
  - Monitors: fetch gaps, games affected by gaps, stale upcoming markets, thin markets, duplicate or swapped games, unmatched team names, single-book outlier moves.
  - 23 tests, including a replay of the 25–26 Oct outage shape (40.8 h gap closes markets; a game kicking off inside the gap has no observed close).
- **Dev-server package** (`arbitredge_v4_devhost/`): bootstrap, verification, Odds API gateway (6,000/month V4 cap, 2,000-credit V3 floor, live off by default), key and backup transfer, production disk survey and plan.
- **Process note:** a stale `.pyc` once made a restored file run its mutated version. All mutation checks were re-run with `PYTHONDONTWRITEBYTECODE=1`, and all 18 were caught.
- **Totals:** 129 V4 tests passing (64 sportsbook + 42 edge + 23 market).

**Blocked on owner:** droplet approval; GitHub repository; SIMS code.

## 2026-10-27: Top-25 rankings, synthetic book on real quotes, automatic site

- **CFB AP Top-25** (`data_v4/rankings.py`, workflow `cfb-rankings`, off until the owner adds a free CollegeFootballData key and sets `CFBD_ENABLED=on`). Each game uses the poll fetched before kickoff and no more than 10 days old. Missing or unmatched rankings mean UNAVAILABLE, never guessed.
- **Synthetic sportsbook on real collected quotes** (`sportsbook_v4/live_book.py`):
  - separate persistent populations, 10,000 NFL and 10,000 CFB bettors; CFB Top-25 games only;
  - the offered price is the latest real quote no older than 6 hours, and nobody bets while no fresh quote exists;
  - tickets come from wager counts and handle from stakes; Need comes from liability;
  - sensitivity bands from 4 behaviour variants; audit hashes per run.
  - All output is labelled MODELED. First real run: 333 modeled NFL wagers (one collection snapshot so far).
- **Automatic site** (`web_v4/live_site.py`, workflow `site-build`, release `v4-site`):
  - rebuilt only after an `odds-collect` run that saved new odds, or after new rankings; about 25 seconds per build, no secrets;
  - the page is self-contained, with an NFL / CFB-Top-25 switch, 8 sections plus health, game detail and filters;
  - LIVE / DELAYED / STALE labels are computed in the browser, so an old page marks itself stale.
- Tests: 295 passed, 21 skipped (older demo-site page tests that need a local nflverse build). Mutation checks: 11 of 11 deliberately broken copies were caught.

## 2026-10-28: CFBD connected; first real Top-25 verification

- **CFBD.** The owner added `CFBD_API_KEY` and `CFBD_ENABLED=on`.
  - A manual `cfb-rankings` run authenticated and saved `rankings_20261028T133307Z.json`.
  - Contents: AP Top 25 for 2026 regular-season weeks 1–9 and 138 FBS teams. No key-like strings are in the snapshot.
  - Cost: 3 free-tier calls, 0 Odds API credits.
- **Found and fixed:**
  - **Name matching.** 3 provider names did not match (Southern Mississippi, San Jose State, Appalachian State). The fail-safe marked them UNAVAILABLE. Matching now uses CFBD alternate names plus the mascot, with accents and punctuation removed; a name that fits two schools stays unmatched.
  - **Next week's games.** These were "UNAVAILABLE: poll older than 10 days". They are now **PENDING**: next Sunday's poll applies, and current ranks are attached only as provisional. Bowl season uses the final poll for up to 45 days.
  - **Need reference line.** It was empty between collection runs. It now uses the consensus at the game's last observation, labelled with that time.
  - **Sharp money.** All 7 Top-25 college SIGNALs were artifacts: snapshots 12 h apart made every move look like "steam within 15 min" or "X moved first".
    - Steam and first-mover claims now require observations close enough in time to show them; otherwise the row reports "timing unresolved".
    - College football needs a 1.0-point minimum move, against 0.5 for the NFL (unvalidated).
    - Result: 0 of 36 Top-25 college and 0 of 58 NFL game-markets show evidence. This is correct at the current cadence.
- **Top-25 slate (Oct 31):** 18 of 54 candidate games are in scope: 17 ranked vs unranked, 1 ranked vs ranked (Arizona #22 @ Texas Tech #13). The book's college run on real quotes has 1,151 modeled wagers; every market is LOW sample except two spreads.
- **Model observation, not changed:** retail books dealt Indiana and Texas 1 point off the market makers. The simulated sharps (3% of bettors) bet into those lines early in the week, so their handle dominates those two Needs. The simulated book never moves its line in response to its own liability, an unvalidated simplification.
