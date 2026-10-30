# ArbitrEdge V5 r1: release plan and report (draft, 2026-10-29 evening)

| | |
|---|---|
| Branch | `v5-dev`, based on `31c2ef3` (the post-game V4 deploy: settlement, bankrolls, build-time fix) |
| Status | **Not deployed.** Deployment = fast-forward `v4-dev` to the approved `v5-dev` commit, **only after the owner's explicit approval** |
| Target | Friday Oct 30 night, before Sunday's NFL slate |

## 1. What V5 r1 is

**Same infrastructure, same website, same 20,000 persistent customers.** Engine `sportsbook_v5.r1` replaces `sportsbook_v4.m4` as the active engine of the existing pipeline:
- collection (Expanded) is unchanged;
- the ledger (`v4-book`), settlement (`v4-settlement`) and Need Tracker (`v4-need*`) are reused;
- the dashboard is the existing Cloudflare Pages site;
- there is no new service, site or subscription.

### Implemented in r1

| Feature | How |
|---|---|
| Persistent identities | the same customer ids, opening bankrolls and base traits as m4 (unchanged populations) |
| Individual differences within the seven groups | per-customer traits from a seed of the customer's own id: engagement, market mix (Dirichlet around the group mix), weekly timing shape (blend of the group profile with "steady"), price tolerance (±1.5 pts implied probability), primetime/national-TV pull, how far ahead of kickoff they look, share of bankroll risked per week |
| Weekly participation and inactivity | each customer is independently active or sits out each week (P = 0.10 + 0.85 × engagement); a busted customer (below 2 × minimum stake) cannot bet |
| Stakes from current bankroll | stake and weekly budget scale with the **week-open bankroll**: opening bankroll + settled P&L of the customer's V5 wagers from earlier weeks; frozen per run, stored in the ledger, part of the run id |
| Settlement and carry-forward | V4 settlement code settles V5 wagers identically; week-open bankrolls come from those settlements. First V5 week = opening bankrolls; **no historical bankroll activity is invented** |
| Immutable wagers, actual accepted prices, exact outcome P&L, Need, Need Tracker | unchanged code paths; cohort rules unchanged; cohort results are additionally split by engine |
| Incremental execution | the full from-scratch replay check runs at most every 12 h per run; every build still hash-verifies the whole ledger; sensitivity replays are cached 3 h |
| Behaviour monitoring | Health tab: participation, wagers per customer, stakes by group, bankroll turnover, concentration, market mix, pass reasons (price sensitivity) |

### Deferred (not claimed; not built)
- House-specific pricing and margin; limits that react to customer results; maximum-payout limits; liability-based line moves. r1 still offers m4's representative real-market quote, with its 10-minute re-quote lag and copy-the-market-makers response to sharp action.
- Parlays and same-game parlays.
- Portfolio-level exposure and parlay-adjusted Need.
- Intra-week bankroll updates. Bankrolls update at each week's open, not after Thursday's game.
- Responses to wins and losses (chasing, tilt, house-money effect, reloads). Only the effect of bankroll size is modelled.
- The kickoff-relative "last-hours rush" (the owner's earlier choice).

## 2. Transition: this week's V4 wagers and Sunday

**Mechanism:** the existing supersession rule.
1. When V5 deploys, the current week's V4 (m4) runs are **kept, immutable and reported as superseded**.
2. Each sport opens a **new V5 run for the whole week**, catching up from Tuesday 12:00 UTC using only the odds observed at each simulated moment, exactly as m4 did when it first opened. Nothing is mixed: each run is one engine's ledger.

**Records:**
- **Sunday's games:** the dashboard, Need and Need Tracker snapshots use the V5 run from the first V5 build on.
- **Tonight's TNF:** its decision was sealed from m4 before kickoff and stays the record. V5 makes no TNF snapshot, because V5's TNF simulation would run after kickoff and snapshots must precede kickoff.
- **Cohorts:** rules unchanged; results shown overall and per engine.
- **Bankrolls:** m4 bankrolls stay in m4's settlement history; V5 bankrolls start at the opening values. Neither invents history.

**Options considered:**
- **(A)** Start V5 next Tuesday. Clean, but misses Sunday.
- **(B)** Mid-week handoff with V4 and V5 wagers in one book. Rejected: it mixes two customer models.
- **(C)** The above. **Recommended** if the release checks pass.

## 3. Release requirements: status

| # | Requirement | Status (Oct 29, 21:20 UTC) |
|---|---|---|
| 1 | Complete regression tests | full suite with V5 active: **497 passed, 22 skipped** (Oct 29 21:25 UTC); to be re-run on the final release commit |
| 2 | Wager and liability calculations | `release_check`: 32 V5 games, book P&L vs per-wager settlement over a 21 × 16 score grid: **0 mismatches** |
| 3 | Deterministic replay, no duplicates | two independent builds byte-identical; from-scratch replay = ledger; 3,975 wager ids unique: **pass** |
| 4 | V4 history intact | all 4 existing runs, 2,922 wagers unchanged after a V5 build: **pass** |
| 5 | GitHub / Cloudflare budgets | builds: 43 s full / 23 s cached locally (GitHub runners are faster); expected ≤ 1 billed minute for most builds. Deploy count unchanged. To confirm on the first real V5 builds. |
| 6 | Website deploys V5 | same workflow and page; local render of every tab: no errors. Remaining: first real build on GitHub after approval. |
| 7 | Rollback plan | §4 |
| 8 | Implemented vs deferred | §1 |

**Need stability** (built): each game's spread Need is recomputed under the 4 alternative customer assumptions. This week on real data: NFL 6 of 14 games agree 4/4, 7 agree 3/4, 1 agrees 2/4; CFB 11 of 18 agree 4/4, 7 agree 3/4. Shown per game.

**Still to do before asking for approval (Friday):**
- re-run `release_check` with tonight's real TNF result (settlement and bankroll paths exercised on real scores);
- re-run the full suite on the final commit;
- Friday's sanity review of the behaviour metrics.

## 3b. Owner's additional requirements (Oct 29 evening)

| # | Requirement | Status |
|---|---|---|
| 1 | Tuesday–Friday V5 wagers labelled reconstructed | **Done.** Every V5 run records its real opening time. Wagers simulated for moments before it are counted and labelled RECONSTRUCTED (run audit, each game on the Need tab, each Need Tracker snapshot's `ledger_basis`). Wagers after opening are prospective (simulated at the next build after their time). |
| 2 | Only information available at placement | **Verified on real data:** 1,053 V5 wagers, every quote used was fetched at or before its offer time, which is at or before placement; 0 violations. The engine has no score input (NoFacts); week-open bankrolls use only settlements of earlier weeks (tested); no closing prices are read by the engine. |
| 3 | V4 and V5 records separate | **Done.** The Need Tracker builds one record per engine (cohorts, all-Need, research subset, breakdowns) and never combines them; m4's TNF decision stays in the m4 record. |
| 4 | Real Thursday settlement, reconciliation, replay, no duplicates | replay and no-duplicates pass; **settlement on the real TNF score: Friday** (results ingest ~9:23 am ET) |
| 5 | Behavioural plausibility | §3c |
| 6 | Need stability prominent | **Done.** A STABLE / FRAGILE NEED badge on every game in the Need tab and the Need Tracker, naming the assumptions that flip the side; also stored in each pre-kickoff snapshot (informational; cohort rules unchanged) |
| 7 | Billed minutes, free-tier capacity, Cloudflare | **Friday** (GitHub job timings for October; Cloudflare deploy count) |
| 8 | Rollback without deleting or corrupting V5 records | **Verified:** an m4 build on the V5 ledger re-activates m4; V5 runs are kept unchanged and shown as superseded |
| 9 | Sunday slate complete, timestamped, Need accurate | **Verified now:** 12 Sunday games and 32 games overall have V5 wagers, all before kickoff; Need computed for every game with wagers; liability exact. Activity through Sunday accrues at each build until kickoff. |

## 3c. Plausibility review: what is unsupported

Measured this week (V5, real odds, data through Oct 29 10:00 UTC). These are outputs; nothing is tuned toward a target.

| Measure | V5 | Comment |
|---|---|---|
| Weekly active customers | 51–52% (casual 44%, sharps 79%) | **Unsupported assumption** (engagement distributions). Published account studies show heavy-tailed activity, but no source gives a weekly NFL participation rate for a large US book. |
| Wagers per betting customer | median 1, max 2–3 (mid-week) | will rise through Sunday; plausible shape, magnitude unverified |
| **Sharp share of spread handle** | **NFL 44%, CFB 40%** (5–6% of tickets) | **Probably implausible for a large recreational book.** Real books limit sharp accounts heavily, and V5 r1 has no house limits beyond m4's flat $3,000 sharp maximum; sharps also participate most weeks. *Owner decision:* launch as is (labelled), or a V5 house-policy setting such as a lower sharp limit (a behaviour change needing approval; not applied). |
| High-stakes recreational share | 41–45% of handle from ~5% of tickets | direction plausible (VIP concentration); size unverified |
| Casual share | ~50% of tickets, ~8% of handle | consistent with small typical stakes ($20 median); unverified |
| Top 10% of customers' share of handle | 83–85% | very concentrated; unverified |
| Arrivals passing on price | 0.5% NFL, 2.6% CFB | price sensitivity is a cap, not a curve; deferred |

## 3d. Investigation: tonight's sharp share (1.7%) vs the m4 NFL-wide 19%

**Data:** the live m4 NFL run through Oct 29 23:33 UTC (7:33 pm ET), read-only.

| Game (ET kickoff) | Spread tickets | Spread handle | Sharp ticket % | Sharp handle % | Unique sharps | Avg sharp wager |
|---|---|---|---|---|---|---|
| Panthers @ Packers (Thu 8:15 pm) | 577 | $66,465 | 0.2% | 1.5% | 1 | $975 |
| Vikings @ Lions (Sun 1 pm) | 47 | $2,256 | 0.0% | 0.0% | 0 | – |
| Colts @ Jaguars | 58 | $6,888 | 0.0% | 0.0% | 0 | – |
| Falcons @ Buccaneers | 43 | $6,018 | 4.7% | 29.7% | 2 | $895 |
| Ravens @ Bills | 53 | $24,871 | 0.0% | 0.0% | 0 | – |
| Cardinals @ Cowboys | 48 | $2,312 | 0.0% | 0.0% | 0 | – |
| Raiders @ Jets | 56 | $5,606 | 1.8% | 3.7% | 1 | $206 |
| Browns @ Steelers | 57 | $9,717 | 1.8% | 14.9% | 1 | $1,450 |
| Titans @ Bengals | 51 | $13,477 | 5.9% | 44.1% | 3 | $1,981 |
| Chargers @ Rams (4:05 pm) | 50 | $7,677 | 2.0% | 23.2% | 1 | $1,783 |
| Chiefs @ Broncos (4:25 pm) | 43 | $5,852 | 4.7% | 53.4% | 2 | $1,564 |
| Patriots @ Dolphins (4:25 pm) | 50 | $3,519 | 0.0% | 0.0% | 0 | – |
| Eagles @ Commanders (Sun 8:20 pm) | 68 | $7,463 | 2.9% | 47.5% | 2 | $1,772 |
| Bears @ Seahawks (Mon 8:20 pm) | 37 | $10,459 | 0.0% | 0.0% | 0 | – |
| **All spread** | 1,238 | $172,580 | 1.1% | **10.9%** | 13 tickets | |

**Other markets:** sharp share of totals handle 42.4% (27 tickets); moneyline 0%. Sharp wagers by day: Tue 9, Wed 6, Thu 25. Arrivals that found no edge: 301.

**Finding: normal game-level variation plus timing. Not a classification, participation or accounting error.**
1. **Simulated sharps bet only when the book's number trails the market makers'** (the re-quote lag), and the book then copies the makers on that market. So sharp action is rare and lumpy: 13 spread tickets all week. One $1,000–2,000 ticket decides a game's sharp share, hence the range from 0% to 53%.
2. **Tonight's game drew by far the most recreational money** (577 tickets, $66k: a standalone primetime game in its final hours). Its single $975 sharp ticket is therefore 1.5%.
3. **The 19% figure was NFL-wide at Thursday 6 am ET.** Recreational money keeps arriving through the week while sharps act early, so the NFL-wide spread share has since fallen to 10.9% and will keep falling until Sunday.
4. **Classification is consistent:** "sharp" = the SHARP archetype in the ledger, the Need Tracker and the audits alike; ticket and handle totals reconcile with the ledger.

**V5 participation vs m4** (same games, same odds, data through Thu 10:35 UTC):

| | m4 | V5 |
|---|---|---|
| Sharp spread tickets / unique sharps | 7 / 7 | 9 / 9 |
| Average sharp wager | $1,304 | $1,989 |
| Recreational spread handle | $39,414 | $22,955 |
| **Sharp share of spread handle** | **18.8%** | **43.8%** |
| Active this week (V5): sharps / high-stakes rec / casual | (everyone, every week) | 79% / 57% / 44% |

**Finding:** V5 does inflate sharp influence, mainly through **participation**:
- Sharps are assumed to show up most weeks (engagement mean about 0.8) while casual customers show up 44% of the time.
- Each active customer keeps m4's per-week arrival rate, so recreational volume falls by about 40% while sharp volume barely changes.
- The larger average sharp wager comes from about 9 tickets, which is within small-sample noise.

This is a consequence of an **unsupported assumption**: m4's arrival rates were never defined as active-week rates. It is not a target, and nothing has been changed.

**Option for the owner (not applied):** define the weekly arrival rate as each customer's long-run average, so an active week's rate = rate ÷ the customer's own P(active). Total volume per customer then matches m4 and only *when* customers bet changes. This would not force any sharp percentage; the share would remain an output.

## 3e. Kickoff sealing independent of builds (owner request, Oct 29 night)

**Safety of the existing mechanism (m4, live):**
- A decision is the last snapshot whose data time **and** build time are before kickoff.
- No build after kickoff can create such a snapshot, and the engine never accepts a wager at or after kickoff.
- So a late seal produces exactly the decision an immediate seal would have: delay affects *when* the record appears, never its content.

**V5 improvement (built on `v5-dev`, ships with V5):**
- **Where it runs:** a no-secrets step at the end of every odds-collect job (`sportsbook_v4/seal_run.py`, `.github/scripts/seal_step.sh`).
- **When it acts:** once a priority game's kickoff is at least 20 minutes past, it seals that game from the immutable snapshots, without waiting for a dashboard build or a Cloudflare deploy. The grace period covers a dashboard build that started before kickoff and is still uploading its final snapshot.
- **Most runs do nothing:** it checks the collector state first, with no downloads.
- **Same rule everywhere:** it uses the same sealing rule as the dashboard build. If both seal a game, the two records must freeze the same snapshot (tested); a mismatch is an integrity error. The earliest seal is kept.
- **Left to the dashboard build:** games with no snapshot at all, since only the build knows the book's full game list.
- **Cost:** no extra jobs, no extra deploys. It adds a few seconds to some collect jobs, which stay within one billed minute.

## 4. Rollback to the last known-good V4

1. Set `ENGINE_VERSION` back to `"sportsbook_v4.m4"` in `sportsbook_v4/__init__.py` (one line; this exact change is the `rollback_to_m4` release check). Commit and push normally to `v4-dev`; no force-push, no history rewrite. The build pacing and kickoff-sealing changes stay in place.
2. The next build re-activates m4. Its weekly runs resume from their watermark and catch up deterministically.
3. V5 runs become superseded: kept, immutable, shown under Health. Their Need snapshots remain on record with their engine tag.
4. **Nothing is deleted.** V4's ledger, settlements, snapshots, decisions and cohorts are untouched throughout.
5. Time to roll back: one commit and one build, about 2 minutes.

## 5. Approval needed

After the Friday checks pass, explicit approval to **replace m4 with V5 r1** on the live site (fast-forward `v4-dev` to the release commit), with the transition in §2.

## 6. Final verification (Fri 2026-10-30, 10:10 am ET)

**Real game.** Panthers @ Packers, final 27-19 (nflverse `2026_08_CAR_GB`, `final: true`). The scheduled `odds-analyze` run had not yet
started, so the score was ingested locally with the production code (`python -m data_v4.results --sport nfl`), unpublished; the official
file follows from that run and goes through the same hash checks.

| | V4 m4 (live) | V5 r1 |
|---|---|---|
| Wagers on the game / settled | 1,110 / 1,110 | 643 / 643 |
| Handle | $134,688.00 | $59,818.00 |
| Win / loss / push | 614 / 496 / 0 | 332 / 311 / 0 |
| Book P&L | -$13,136.37 | -$12,020.01 |
| Customer P&L (sum) | +$13,136.37 (1,020 customers) | +$12,020.01 (585 customers) |
| Independent re-grade, cent differences | 0 / 0 | 0 / 0 |
| Accepted at or after kickoff | 0 | 0 |

No line was on a push number (spreads 3.5/4, totals 47/47.5; margin 8, total 46); pushes, moneyline ties, corrections, missing scores
and tampering are covered by `tests_v4/test_bettor_settlement.py`. Settlement records every wager, superseded runs included; bankrolls
and reconciliation count the active engine's runs (game and customer mismatch: 0 cents).

**Sealed decision.** Unchanged: snapshot taken 23:33:01Z / built 23:33:23Z, `record_hash 05c40a8e178919a5...`; C2 eligible, pick
Carolina +3.5 -110 (fanatics); C1, C3, C4 not eligible. Graded: ATS loss, -1.00 unit.

**Release checks** (`docs_v5/release_check_2026-10-30.json`): all 9 pass. 4 pre-V5 runs / 8,149 wagers intact; 11,930 wager ids unique;
two independent V5 builds byte-identical and a from-scratch replay equal; liability exact for 32 games; 3,781 V5 wagers, 0 information
violations; 32 games with wagers, 12 Sunday NFL games, 0 wagers at/after kickoff, Need computed for every game with wagers; m4 rollback
keeps every V5 record.

**Tests.** Full suite on `v5-dev`: 502 passed, 22 skipped.

**Reconstructed vs prospective.** Every V5 wager accepted before the run's `opened_at` (the first V5 build after deployment) is labelled
RECONSTRUCTED from historical observed odds; later ones are PROSPECTIVE. In the 14:10Z rehearsal: NFL 1,690 reconstructed / 56
prospective, CFB 2,041 / 53 (prospective counts there are an artifact of rehearsing several builds; in production everything before
tonight's deployment build is reconstructed).

**GitHub Actions (private repo, GitHub Free 2,000 min/month shared across the account).** Measured job durations: collect 7-17 s
(1 min), dashboard build 55-91 s (2 min; V5 equals V4: build step about 62 s cold / 35 s warm locally), analysis 23 s (1), rankings
11 s (1), CI 133-289 s (3-5). Worst-case month after the pacing change (3 h quiet, cap 280/300): collect 1,035 (every scheduled run
starting, which GitHub never achieves) + builds 600 + CI 160 (40 pushes x 4) + analysis 31 + rankings 27 = 1,853, tested <= 1,900.
Not in that figure: manual reruns and `workflow_dispatch` builds (2 min each), CI above 40 pushes, and other private repositories
on the account (for example the SIMS backup, if it runs Actions). Realistic month: about 1,550-1,700. October to date: about 200.

**Cloudflare / Odds API.** At most ~309 deployments a month (Free plan 500). Credit gate, caps (5,000 / 250), V3 floor 2,000,
breaker and free preflight unchanged; V5 adds no Odds API calls.

## 7. Deployment procedure (after explicit owner approval only)

1. `git push origin v5-dev:v4-dev` (a fast-forward: `v5-dev` contains every `v4-dev` commit; no force).
2. CI runs the suite on `v4-dev` (about 4 billed minutes).
3. Start one `site-build` (workflow_dispatch) so V5 opens now rather than at the next odds save. It opens the V5 weekly runs,
   reconstructs Tuesday-Friday from the stored observed odds, verifies replay, deploys behind Cloudflare Access.
4. Verify: page header "ArbitrEdge V5"; audit `engine_version sportsbook_v5.r1`, `replay_verified: this build`; V4 runs listed as
   superseded with unchanged counts; settlement status OK; Need Tracker shows V4 and V5 sections separately; Access still required.
5. If any check fails, roll back (section 4).
