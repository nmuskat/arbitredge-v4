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

## 4. Rollback to the last known-good V4

1. Set `ENGINE_VERSION` back to `"sportsbook_v4.m4"` (one line), or revert `v4-dev` to the pre-V5 commit `31c2ef3`. Push normally; no force-push.
2. The next build re-activates m4. Its weekly runs resume from their watermark and catch up deterministically.
3. V5 runs become superseded: kept, immutable, shown under Health. Their Need snapshots remain on record with their engine tag.
4. **Nothing is deleted.** V4's ledger, settlements, snapshots, decisions and cohorts are untouched throughout.
5. Time to roll back: one commit and one build, about 2 minutes.

## 5. Approval needed

After the Friday checks pass, explicit approval to **replace m4 with V5 r1** on the live site (fast-forward `v4-dev` to the release commit), with the transition in §2.
