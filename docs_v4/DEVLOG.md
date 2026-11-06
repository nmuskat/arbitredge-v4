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

## 2026-10-28: Cloudflare Pages (owner-only) live

- The first deploys came from `site-build` started by hand: run 40760306951, then run 40761149952 with the audit report. Both succeeded.
- The report (`v4-site/deploy_report.jsonl`) shows HTTP 302 to the Cloudflare Access login for:
  - `arbitredge-v4-staging.pages.dev` and a random `*.arbitredge-v4-staging.pages.dev` address, before upload;
  - the production address and the new deployment's own URL (`4dcdff47.…`), after upload.
- Production branch `main` (Direct Upload project). Deployment `4dcdff47-848a-4a20-904e-ef4514770b67`, status success.
- From now on every `odds-collect` run that saves new odds, and every new AP poll, rebuilds and redeploys the page automatically.
- Viewing the page after login can only be checked by the owner.

## 2026-10-28: Pipeline audit; persistent synthetic-book ledger

- **Staleness is normal cadence, not a failure.**
  - Every scheduled `odds-collect` run succeeded; GitHub starts them 40–50 min after the cron time.
  - Fetches with quotes on Oct 28: NFL 01:07 UTC; college 01:07 and 15:58 UTC.
  - With the test budget, NFL is due again when the next kickoff is within 24 h, so the next NFL fetch is at the 00:17 UTC slot on Oct 29. The page correctly showed STALE.
  - `site-build` ran after both fetch runs; runs that did not fetch skip it, by design.
- **Found and fixed:**
  - **Wagers were not persistent.** The synthetic book's run was keyed to "games still upcoming within 8 days", so a kickoff or a newly listed game would have re-drawn every wager.
    - Now: one fixed run per sport per betting week (Tue 12:00 UTC to the next Tuesday); membership is fixed when the week opens.
    - Each advance is saved as an immutable file in release `v4-book`.
    - Each build reloads the files (hash- and gap-checked), re-simulates the week from scratch, and requires the identical wager set before exporting anything new.
  - **The book advanced to build time.** It now advances to "data complete through" = max(last observation, build time − 15 min).
  - **Odds newer than the build time.** A page built as of an earlier time could use them, and ages could show as negative. Fixed.
  - **Moneyline sharp evidence** was not analysed. It now is, with sport thresholds of 1.5 / 2.0 probability points (unvalidated).
- **Limitations, not changed (they need credits, so owner approval):**
  - The NFL book trades only about 6 h after each ~24-hourly NFL fetch.
  - No close-snapshot attempts for the Sun 11-08 14:30 UTC NFL game, or for several weekday-night non-Top-25 college games.
- Tests: 358 passed, 21 skipped. Mutation checks: 9 of 9 new ledger protections caught.

## 2026-10-28: Synthetic sportsbook audit; engine m4

See `docs_v4/SPORTSBOOK_AUDIT.md`.

- **Verified on the real ledger:**
  - 20,000 persistent identities (hash-verified);
  - every accepted price equals the real quote at its offer time;
  - an independent recomputation of splits, payouts, book P&L and Need matches the dashboard (0 mismatches).
- **Fixed (engine m4, no API spend):**
  - a one-sided offered line, always the lower middle value;
  - offered price pairs mixed from two books;
  - a single-operator sharp reference;
  - no book reaction to sharp action. In m1 that left 89–95% of handle sharp and 100% on underdogs; m4 gives about 47%, on both sides.
  - Also: standalone (primetime) games are now detected from the schedule, and payouts and book P&L are shown per side.
  - m1 runs stay in `v4-book`, unchanged and marked superseded.
- **Collection plan** (planning only):
  - the current test tier projects to about 509 credits a month and will meet its 500 cap;
  - Top-25-targeted CFB closes give about 340;
  - balanced about 757, aggressive about 979.
  - Awaiting owner decision.

## 2026-10-29: Balanced tier, Top-25 CFB closes, Sunday-morning window (owner-approved)

- **Schedule changes:**
  - **Rule-based tiers:** `test` is unchanged; `balanced` is (6 h / 2 h, 24 h / 4 h, 72 h / 8 h, 168 h / 24 h); `aggressive` is planning only. Closes are always on.
  - **CFB priority kickoffs:** games with an AP Top-25 team under the applicable poll. Games whose ranking is unknown also count, so a missing poll never silently drops a Top-25 close. The collector stores each sport's games, and the collect job reads the AP snapshots.
  - **Sunday window:** cron now runs 12:00–23:45 UTC on Sundays (international NFL kickoffs).
- **Real-schedule projection** (`tools_v4/consumption_check.py`, from Oct 29 00:45 UTC):
  - balanced: 177 credits in 7 days (59 fetches); peak day 51 (under the 60/day limit); about 770 a month;
  - test, for comparison: 81 credits.
- **Budget:** the gateway caps, the per-request limit and the V3 floor are unchanged and fail closed (tested again under balanced with a 3-credit cap).
- **Activation:** the owner sets `V4_TIER=balanced` and `V4_MONTHLY_CAP=1000`. Variables cannot be changed from this environment, and the rules reserve them for the owner.
- **Settlement and bankroll plan:** `docs_v4/SETTLEMENT_BANKROLL_PLAN.md`.

## 2026-10-29: Expanded tier built (not active); fail-closed balance preflight

- **Expanded tier** (details in `docs_v4/EXPANDED_TIER_PLAN.md`):
  - kickoff-relative cadence: 90 min / 15 min, 6 h / 30 min, 24 h / 1 h, 72 h / 2 h, 7 days / 4 h;
  - opener, early-week and injury-report calendar windows;
  - quiet-market backoff, never applied to closes;
  - piggyback fetches;
  - an hourly cron layer that the workflow skips unless `V4_TIER=expanded`;
  - an hourly dashboard-rebuild throttle (always rebuilt after a close).
- **Projection on the real upcoming schedule:** about 3,340–3,730 credits/month; peak day 186; worst rolling 24 h 198. NFL about 70 / 34 / 18 / 6 snapshots per game in the final 72 h / 24 h / 6 h / 90 min. All closes observed.
- **Actions minutes** are about 2,000–2,070/month: at or over GitHub Free's 2,000. Options are A/B/C in the plan.
- **Safety, live now for every tier:**
  - a free `/v4/sports` balance check before each paid request, failing closed;
  - fixed: the V3 floor was skipped when no balance had been recorded yet this month (fail-open);
  - fixed: on equal timestamps, the "last balance" lookup could pick the older row.
- **Proposed when activating:** `V4_MONTHLY_CAP` 5000 and `V4_DAILY_CAP` 250. Owner decision.

## 2026-10-29: Expanded tier fitted to GitHub Free and Cloudflare Free (still not active)

**Constraint.** The owner requires GitHub and Cloudflare to stay $0/month. Before this change, Expanded projected ~2,000–2,070 Actions minutes a month, at or over GitHub Free's 2,000.

**Collection schedule:**
- 238 scheduled runs a week under expanded (was 276).
- Idle Tuesday and Wednesday daytime hourly runs removed.
- 14 redundant 3-hourly runs a week skipped only under expanded.
- **Bug fixed:** 12 duplicate same-minute runs a week (overlapping cron entries).
- Snapshot counts and closes are unchanged in simulation.

**Dashboard rebuilds** (`schedule_policy.site_due`, every tier):
- 2 h when quiet; 45 min in the final 6 h before a priority kickoff;
- always after closes; never without new odds;
- monthly self-limit 380 (soft) / 440 (hard).

**Build speed: 69 s → 30 s locally, output byte-identical** (page, data file, ledger wagers):
- one price lookup per odds state;
- the population generated once per parameter set;
- exact score-distribution and fair-line memos.

An intermediate version of the population cache ignored the sensitivity variants and changed the bands. It was caught by the byte comparison and fixed; a test now covers it.

**CI:** skipped for documentation-only pushes.

**Projection (expanded):**
- ~3,280–3,570 credits a month;
- ~1,610–1,750 Actions minutes a month;
- 374–413 Cloudflare deployments a month.

**Cloudflare documentation:** the 500 builds/month limit is described for Git builds; whether direct uploads count isn't stated, so the design assumes they do.

**Tests:** see the commit. Activation remains the owner's decision (`docs_v4/EXPANDED_TIER_PLAN.md` §5).

## 2026-10-29: Expanded tier activated by the owner; post-activation check

**Activation.** The owner set `V4_TIER=expanded`, `V4_MONTHLY_CAP=5000` and `V4_DAILY_CAP=250`. The first executed run in the Expanded schedule was the 03:52 slot (started 04:26:56Z).

**04:26Z to 12:22Z:**
- Runs: 5 collect runs succeeded; 1 standard-only slot skipped by design (06:17); no failures; no dropped slots.
- **Credits:**
  - 8 paid fetches, 24 credits;
  - each preceded by a free `/v4/sports` balance check (cost 0);
  - provider balance 19,940 → 19,916, exactly 3 per fetch;
  - no refusals.
- **Dashboard:** 3 builds (04:27, 07:44, 10:00), 2 h apart as designed for quiet periods. Each Cloudflare deploy succeeded, with Access confirmed on the main address and the previews, before and after upload.
- **Ledger:** all earlier wagers still present, unchanged. m4 NFL 183 → 530, CFB 897 → 1,005; replay verified.

**Bug fixed: GitHub start jitter halved the hourly cadence.** The 04:52 slot started 58 min after the previous fetch, so the 1-hour rule skipped it. Gaps now count as met within 20% (at most 15 min).
- Simulated with random 5–50 min delays: 2,590–3,075 credits a month, against 2,450–2,880 before the fix.
- Worst 24 h ≤ 177; all closes observed.

## 2026-10-29: Book Need Tracker (research / paper only)

**Owner-approved design; see `docs_v4/NEED_TRACKER.md`.**

**Audit.**
- Ready and reused: the ledger, exact per-outcome book P&L, and grading.
- Missing, now built:
  - persistent pre-kickoff records (`prospective.py`'s SQLite table does not survive between Actions runs);
  - live final scores;
  - recreational / sharp / concentration metrics.
- Bettor settlement and bankrolls are not needed for this tracker.

**Built.**
- Pressure metrics and the pre-registered five-rule filter.
- Need/fade alignment: DISAGREE is flagged, not picked.
- Immutable snapshots (`v4-need-YYYY-Www`) and sealed decisions (`v4-need-decisions`).
- Final scores: NFL via nflverse in `odds-analyze`; CFB via CFBD in `cfb-rankings` (owner-approved key use).
- Grading at the recorded price with confidence intervals.
- The Need Tracker tab.

**Classification v2.**
- NFL national TV = standalone; CFB primetime = 7 pm – midnight ET.
- Applies to weekly runs opening from 2026-11-03 only.

**Verified.**
- Snapshot of Panthers @ Packers from real data: ALIGNED (Need: Carolina +3.5 −108). Not qualified: one bettor holds 33.4% of the spread handle.
- Tracker records are byte-reproducible.
- Ledger hash and splits are identical with and without the tracker (NFL 530, CFB 1,005 wagers).
- Mutation checks: the alignment and wall-clock rules are caught by tests.

**Exploratory historical test (synthetic, not evidence).** NFL 2016–2025, 2,634 games:
- all Need sides 1292-1278-64: 50.3% (95% CI 48.3–52.2), ROI −2.9% (−6.5% to +0.8%);
- research subset 12-11-1 (24 games);
- no trend with pressure strength.

See `docs_v4/NEED_TRACKER_HISTORY.md`. Thresholds unchanged.

## 2026-11-06: Sharp follow list (EXPERIMENTAL) and a replay-portability finding

**Sharp follow list** (`sportsbook_v4/sharp_follow.py`, top card of the Sharp Money tab, owner-requested). For every
spread and total the simulated sharps bet, the side with more sharp dollars, tagged ONE_SIDE / SPLIT /
SHARP_VS_PUBLIC / ALIGNED. Graded from the immutable ledger (wagers before kickoff), final scores and the market line
last observed before kickoff. Patterns were chosen after seeing week 1, so records are split at
`REGISTERED_AT` = 2026-11-06T23:45Z: before = in-sample, after = the only real test. Display only; NO BET.
In-sample (graded at the close): CFB totals ANY 12-5, ONE_SIDE 6-2, SPLIT 6-3, SVP 5-3, ALIGNED 4-1; CFB spreads ANY
9-8; NFL spreads ANY 9-6, totals ANY 8-6.

**Finding, not fixed:** a from-scratch replay of the current weekly runs on this cloud machine diverges from the
stored ledger from 2026-11-05 ~22:00Z (54/51 wagers), with identical inputs (the 158 shards the 13:21Z production
build verified), Python 3.12.3, SQLite 3.45.1 and glibc 2.39. Production's own 12-hourly replay check passes on the
GitHub runner, so production is self-consistent; the engine's floating-point decisions appear machine-dependent
(likely last-bit differences in libm on different CPUs). Risk: a runner on different hardware could flag
INTEGRITY_FAILED. Proposed (needs approval): round the engine's probability/utility comparisons before thresholds.
