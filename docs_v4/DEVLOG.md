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

## 2026-11-07 — Kickoff times: use the feed's latest report, not the earliest ever seen

Owner report: the dashboard showed some CFB games at 11:00 am (Central; noon ET) that kick off later.
Cause: the Odds API lists CFB games without a set time at a placeholder (17:00Z = noon ET this week)
and moves them once TV slots are announced. Every kickoff query used `MIN(commence)`, so the placeholder stuck.
This week: Oregon @ Ohio State (real 3:30 pm ET), Cincinnati @ Houston, Iowa @ Northwestern, West Virginia @
Texas Tech, Kentucky @ Tennessee, Georgia @ Ole Miss and others.

Effects of the bug beyond the display:
- `asof.observed_close` took the "close" at the placeholder, so a moved game's close was reported
  unobserved (or taken hours early) in the line-shopping closing-EV analysis.
- The collection scheduler (`actions_run`) planned close-window fetches around the placeholder, then dropped
  the game as already started.

Fix: `market_v4.asof.kickoffs()` returns each event's kickoff from its latest fetch (books disagreeing: most
common value, ties to the later time; point in time with `as_of`). Used by the board, observed closes,
line-shop events, collection scheduling, the stale-market check and `consumption_check`.

Not changed: the synthetic book (`live_book.week_games`) freezes each weekly run's games, kickoffs included,
when the run opens. Its trading cutoff, Need/Official snapshots and seals use that stored time (immutable
ledger, replay-verified). This week, moved games stopped trading at the placeholder. The dashboard now shows
the feed's time for upcoming games and notes "time moved; sim book closed <time>". A forward-only change
(runs opened after a cutoff use the latest kickoff) is proposed, not built.

## 2026-11-09 — Site build: retry GitHub's temporary download errors

Three dashboard builds in three days (Nov 7 16:13Z, Nov 9 06:50Z and 16:48Z) failed because GitHub's
API answered one release-asset download with HTTP 500. The collection itself succeeded each time; only
the dashboard rebuild was lost (the 16:48Z one would have published Sunday's NFL grades).
`.github/scripts/site_inputs.sh` now wraps `gh` so that temporary errors (HTTP 5xx, dropped connections,
timeouts) are retried up to 5 times with 5/10/15/20 s pauses; downloads retry with `--clobber` so a partly
written file is replaced. Every other failure (e.g. 404 for a release that does not exist yet, or an
incomplete file count) behaves exactly as before. No credits, secrets, variables or data are affected.
Owner approved the deploy ("Deploy").

## 2026-11-10 — One-time historical backfill (built; not run)

Owner approved replaying the synthetic book on the whole season (NFL weeks 1-7, CFB weeks 0-8) with
historical odds, and approved going beyond the 6,000/month ceiling for it. Implemented as a SEPARATE
one-time allowance (12,000 credits max, expires 2026-12-01, usable amount = owner variable
`V4_BACKFILL_BUDGET`) instead of raising the live ceiling: live caps are unchanged and backfill spend is
added back for live collection (`actions_run.exclude_backfill`). Owner-started only: odds-collect
workflow_dispatch input `mode` = backfill-pilot / backfill-all (`collect_v4/backfill_run.py`); same gateway,
preflight, V3 floor, breaker and ledger; per-request ceiling 30 (one 3-market historical snapshot).
Snapshots go to release `odds-hist`, never to odds-data-*. Snapshot plan in `collect_v4/backfill.py`
(Wednesday anchor, daily 16:00 UTC, per kickoff cluster -3 h / -1 h / -10 min; CFB Top-25 by the week's AP
poll). Estimate ~11,600 credits (CFB ~7,050, NFL ~4,560; earlier rough figure of ~7,650 was too low).
Tests: tests_collect/test_backfill.py (13). Full suite: 587 passed, 22 skipped.

## 2026-11-10 — Historical backfill run and season study

Owner ran backfill-pilot (23 snapshots, 690 credits, provider-confirmed) and backfill-all (317 snapshots,
9,510 credits, complete in one 9-minute run). Total 10,200 credits (estimate was ~11,600); allowance left 1,800.
Replay (`tools_v4/backfill_replay.py`, same engine sportsbook_v5.r2 / params d3995c0c5534 / population as live):
NFL weeks 1-7 (107 games) and CFB weeks 0-8 (184 Top-25 games with odds; week 0 had only one), 97,356
simulated wagers, all settled on real final scores. Fix found while replaying: Labor Day Sunday/Monday CFB
games made the live poll rule wait for the next poll and dropped the week; the replay ranks each game by its own
week's poll. Fidelity: midweek snapshots daily (live: every few hours), so ~25-30% fewer early-week wagers.

Book: +$898,439 on $10.44M handle (8.6% hold); NFL +$424,481 (7.0%), CFB +$473,958 (10.8%). Every customer
type lost except two tiny ones; high-stakes recreational customers lost $500k. Simulated sharps LOST
(NFL 234-302, CFB 383-413 on spreads/totals) although they beat the closing line on 366/536 and 484/796 bets.

Study (`tools_v4/backfill_study.py`; 21 fixed situations x 7 slices, graded at the PICK-time (kickoff - 60 min)
consensus line; DISCOVERY NFL wk 1-4 / CFB wk 0-4, TEST NFL wk 5-7 / CFB wk 5-8; "holds" = >=55% with >=25
bets in discovery and > 52.4% in test):
* Sharp vs Public card, bet the sharps (the planned Bet Window rule): 27-38-1 (42%). FAILS.
* Held in both halves (5): NFL spreads fade the public 57-43-7; fade public 50-60% NFL spreads 31-24-5;
  NFL spreads public on the favorite -> bet the dog 39-27-3; NFL totals with 90%+ sharp money 34-26.
* Third check, the two live weeks (graded at the close): fade public NFL spreads 7-18-1; public-on-favorite
  -> dog 3-13-1; NFL totals 90%+ sharp 4-6; favorite 7+ -> dog 2-5. None survives all three.
Verdict: across 16 weeks no side-picking rule built from the simulated customers survives out of sample. The
book's profit comes from the vig and a few large losing customers, not from a predictable side. The only
positive real-data evidence remains line shopping (closing EV). No official plays (rule 6).

## 2026-11-11 — CF1 CFB line-follow tracker (owner-approved; research records only)

- **What:** owner approved adding a frozen prospective tracker to the live site build.
- **Code:** `cf1/tracker.py` (self-contained; `cf1/normalize.py` copied unchanged from research `need_v6/normalize.py`).
- **Rule (RULE_HASH 4723d320…):**
  - CFB spreads and totals;
  - consensus fair line moved ≥ 1.0 pt from the first collected snapshot to the last snapshot ≤ kickoff − 1 h
    (≤ 3 h old);
  - record the side it moved toward at the best price in that snapshot.
- **Records:** write-once pre-kickoff records in release `v4-cf1`, from the site-build step "CF1 …"
  (continue-on-error; no API calls; never places, recommends or shows a bet).
- **Equivalence check:** the production copy reproduces the research module decision-for-decision on all 1,540
  CFB game-markets in the development store (628 identical signals).
- **Development evidence (EXPLORATORY, already-studied 2026 data):**
  - 334-282-4, ROI +5.1% (95% CI −2.3% to +12.3%; p = 0.10);
  - closing value −1.75%;
  - not significant after multiple testing.
- **Judgement after the bowl season (≥ 150 signals):**
  - abandon if ROI ≤ 0% or closing value ≤ −2%;
  - "promising" if one-sided p < 0.05;
  - validated only after a second season.
- **Live-era volume:** 52 and 88 signals in ISO weeks 44–45.
- **Tests:** 592 passed, 22 skipped (required suite + tests_cf1).

## 2026-11-12 — Dashboard build: incremental line-shopping cache (owner-approved; performance only)

- **Problem:** the Build site step grew from about 2 minutes to over 10 minutes; the old 10-minute job limit was hit four times (10–12 Nov). The cause was `line_shop.report` re-scanning every stored snapshot on every build (~83% of the step, growing with every snapshot). The job limit was raised to 20 minutes as a stopgap (f3c29d3).
- **Fix:** `analysis_v4/line_shop_cache.py`.
  - Each game's scan at each snapshot is reused when a SHA-256 fingerprint of that game's quotes up to that snapshot is unchanged.
  - Kickoff times and team names always come from the current data.
  - The cache key includes a calculation version: hashes of the source code and NFL parameters, the policy, the freshness rule and the sport.
  - Every build recomputes a random sample of 25 reused entries per sport. Any difference discards the cache and recomputes everything.
  - A missing, corrupt, truncated or old cache means a full calculation.
  - The cache is saved as the `v4-site` asset `line_shop_cache.json.gz` (~0.7 MB). Publish never overwrites a cache that another build saved after this build downloaded it.
  - The daily `odds-analyze` report is unchanged (no cache).
- **Equivalence on production inputs** (259 shards, through 12 Nov 03:33Z):
  - The full report and the cached report were compared as serialized JSON: every field, including the per-opportunity rows and bootstrap intervals. All identical.
  - Cold cache: identical.
  - Warm cache with one new snapshot (cache built from 258 shards): 99 game-snapshots computed, 18,686 reused, identical.
  - Warm repeat: identical.
  - Results: NFL 286 opportunities, 167 with an observed close, closing EV +1.41% (CI −0.66% to +3.39%), INCONCLUSIVE. CFB 1,124 opportunities, 761 with a close, +1.45% (CI +0.45% to +2.45%), POSITIVE (provisional).
- **Runtimes** (line-shopping report, both sports, local): full 529 s; cold cache 498 s; warm with a new snapshot 15.5 s; warm repeat 12.1 s.
- **Tests:** 18 new scenario tests (`tests_v4/test_line_shop_cache.py`): cold, warm, new snapshots, corrected snapshot (only the affected game and times are recomputed), deleted snapshot, moved kickoff, truncated/garbage/non-gzip/wrong-schema/malformed/empty cache, leftover temp file, code and policy version changes, a tampered entry caught by the spot check, the site CLI round trip, and the workflow wiring. Required suite + tests_cf1: 610 passed, 22 skipped.
- **Production (v4-dev dc7cf2c):**
  - Cold build, run 43155196957: success. 8m03s in total, of which Build site took 6m35s. The cache was created (719 KB).
  - Warm build, run 43156661003: success. 2m21s in total, of which Build site took **64 s**. It added one new shard, computed 99 game-snapshots, reused 18,785 and spot-checked 50, with no differences.
  - CF1 ran normally (no eligible cutoff yet). The synthetic book was OK for both sports. Deploy succeeded.
  - The published Performance figures equal a from-scratch recompute on the same 260 shards, for both NFL and CFB.
- **Job time limit:** it stays at 20 minutes. Return it to 10 after several warm production builds show the same headroom.

## 2026-11-12 — Original ArbitrEdge Market Intelligence merged into production (owner request)

- **Merged:** `research/mi-integration` into `v4-dev` (41678dd). It includes the 2026 Market NEED × synthetic sharp agreement records: 1,534 game rows with complete W-L-P, units, ROI and CLV, frozen from `original_v3/agreement.py`, EXPLORATORY.
- **Tab switch:** the tab appears once the owner sets the repository variable `MARKET_INTEL` to `on`. This session cannot set GitHub variables.
- **Recorder:** `MARKET_INTEL_RECORD` stays off.
- **Local build** on 265 production shards: Market Intelligence step 7.6 s, whole page 2m07s.
- **Production build** 43232081326 (MI still off): success; line-shop cache warm; book OK for NFL and CFB; CF1 ran.
- **Tests:** required suite + tests_cf1 + tests_market_intel: 618 passed, 22 skipped.

## 2026-11-12 — Pinnacle & Circa line-move tab; Circa replaces LowVig in collection (owner-approved)

- **Collection:** `collect_v4/collector.py` now collects `circasports` instead of `lowvig` (a BetOnline mirror). It's still 10 books, so still 3 credits per fetch. Circa also joins the market-maker consensus (weight 3.0, already in `asof.SHARP_WEIGHTS`), so EV Scanner and Need consensus figures may shift slightly from this date. The historical backfill keeps its original book list.
  - The provider's support for Circa on NFL/CFB is unconfirmed until the next collection run. If no Circa prices arrive, the tab shows "no prices yet". Revert if Circa never appears.
- **Tab "Pinnacle & Circa"** (`analysis_v4/book_moves.py`, rule book_moves.v1, fixed before any result was computed):
  - Shows every spread and total price change for each game.
  - INFERRED NEED: the side the book made more expensive since first observation (a move of at least 0.25 point-equivalents), as the owner described from a Circa manager ("book hard to the side we want to need"). Split into LINE (the number moved) and JUICE (price only).
  - Graded at the book's closing number. The season record counts every collected game; the page lists the last 3 weeks.
- **First Pinnacle records** (since 27 Oct):
  - NFL spreads 11-15, totals 11-12.
  - CFB spreads 46-52-2, totals 52-41-1.
  - Small samples; no edge shown yet.
- **Tests:** 4 new tests in `tests_v4/test_book_moves.py`.
- **Update (same day):** the first collection with `circasports` (2026-11-12T15:40Z) returned no Circa prices for either sport, while all 9 other books returned prices. `lowvig` is restored as agreed. The tab keeps its Circa rows, which show "no prices yet".
