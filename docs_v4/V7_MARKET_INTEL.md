# ArbitrEdge V7: professional sportsbook market intelligence (DEVELOPMENT, not deployed)

Branch `dev/v7-market-intel` (built on `dev/need-engine-v6.1`). Rules `intel_v7.0`, CONFIG_HASH `622c296b99ce`.
Prospective spec `docs_v4/V7_PROSPECTIVE_SPEC.md`, sections 1-6 SHA-256 prefix `57f73895e5ea0539` (frozen).

**What V7 is.** A separate analytical layer over the **real collected odds**. It infers which side
**professional pricing** rates higher than the **retail books** do:
- professional: Pinnacle, plus the BetOnline/LowVig operator counted once;
- retail: DraftKings, FanDuel, BetMGM, Caesars, BetRivers, ESPN Bet, Fanatics.

It never touches the synthetic sportsbook, its 20,000 bettors, ledger, settlement, Financial NEED, SIMS or props.
It makes **no API calls** and does not change collection. **Real sportsbook liability is never observed:** every
card says ACTUAL BOOK LIABILITY: UNKNOWN.

## 1. Market-data quality audit (`python -m intel_v7.audit`)
| Sport | Book | Events | Share of event-fetches quoted | Median margin |
|---|---|---|---|---|
| cfb | betmgm | 745 | 71% | 4.3% |
| cfb | betonlineag | 740 | 76% | 4.76% |
| cfb | betrivers | 747 | 81% | 5.43% |
| cfb | draftkings | 766 | 94% | 4.75% |
| cfb | espnbet | 737 | 79% | 4.71% |
| cfb | fanatics | 599 | 70% | 4.76% |
| cfb | fanduel | 756 | 91% | 4.76% |
| cfb | lowvig | 714 | 77% | 3.85% |
| cfb | pinnacle | 744 | 72% | 3.83% |
| cfb | williamhill_us | 661 | 70% | 4.75% |
| nfl | betmgm | 150 | 51% | 4.3% |
| nfl | betonlineag | 163 | 77% | 4.76% |
| nfl | betrivers | 163 | 82% | 4.97% |
| nfl | draftkings | 272 | 100% | 4.76% |
| nfl | espnbet | 160 | 52% | 4.71% |
| nfl | fanatics | 149 | 45% | 4.76% |
| nfl | fanduel | 163 | 83% | 4.75% |
| nfl | lowvig | 163 | 77% | 2.58% |
| nfl | pinnacle | 150 | 46% | 2.99% |
| nfl | williamhill_us | 256 | 92% | 4.75% |

| Era | Fetch interval median (p25-p75) | Last 6 h before kickoff | Provider refresh lag median (max) |
|---|---|---|---|
| Historical backfill | 75.0 min (50.0-360.0) | 50.0 min | 0.48 min (14.67) |
| Live collection | 75.63 min (58.3-124.37) | 40.77 min | 0.5 min (14.88) |

* BetOnline vs LowVig: identical numbers in 98.62% of 50,525 same-fetch pairs: **one operator**, counted once.
* 82.8% of provider refreshes (market_last_update) carry no price change: it is a refresh time, **not** a price-change time. A change is only known to lie inside its fetch window (Pinnacle median 120.0 min, DraftKings 131.37 min).
* 1042 events; 212 had their kickoff time changed (V7 uses the latest reported kickoff and never uses a quote fetched after it); 1 had a team-name change.
* Pinnacle is missing from many fetches (NFL 46%, CFB 72% of event-fetches; NFL denominators include DraftKings' early lines for future weeks). A missing quote is never carried forward.
* No Circa data exists in our collection; none is invented.

## 2. Modules (`intel_v7/`)

All modules are point-in-time: a card at time *t* uses only fetches at or before *t*, and never a quote fetched after
kickoff. "Fresh" means the book was quoted **in that fetch**; nothing is carried forward. Fair values are no-vig and
push-aware: the implied expected home margin / total (`need_v6.normalize`), with NFL key-number weights and plain
CFB. That makes different numbers and juices directly comparable.

| Module | File | What it decides |
|---|---|---|
| History | `history.py` | Per-fetch, per-book normalised quotes; operator collapse; professional fair (Pinnacle, else the BetOnline group); retail consensus (median, needs ≥ 3 books) and its dispersion |
| A. Professional price divergence | `modules.divergence` | MATERIAL when \|pro − retail\| ≥ max(0.5 pt, 2 × retail dispersion). Corroborated when both operators agree. PROS_DISAGREE when they point opposite ways. INSUFFICIENT without a pro quote or ≥ 3 retail books. Reports every book's margin. |
| B. Active resistance | `modules.resistance` | Within an 8 h window: retail moved ≥ 0.5 while a pro operator, **quoted in every fetch**, held (≤ 0.25) or moved the other way. ACTIVE_RESISTANCE needs **both** operators; one held + one followed → INDETERMINATE (possible stale feed); missing quotes or a single fetch → INDETERMINATE. Also reports PRO_LED_MOVE and the **change windows** (a change is only known to lie between two fetches). |
| C. Key-number defense | `modules.key_numbers` | NFL spreads only. Counts line changes vs juice-only changes. KEY_DEFENSE when a pro held 3/7 through the window, moved juice by ≥ 0.03 no-vig, retail moved off the number, **and** the juice-adjusted pro price still differs from retail (≥ 0.25). Otherwise NO_DEFENSE / KEY_HELD_JUICE_ONLY. Secondary numbers (1, 4, 6, 10, 14) are flagged. |
| D. Market convergence | `convergence.py` | Walk-forward over games already kicked off: slopes of the retail and pro moves to the close on the earlier gap, with bootstrap CIs, per sport × market × horizon. Result: PRO_LEADS / RETAIL_LEADS / TWO_WAY / UNRESOLVED / INSUFFICIENT. Pinnacle is not assumed to lead. |
| E. Classifier + card | `classify.py` | Evidence points: A 1 (+1 corroborated), B active 2 / other 1, C 1, D RETAIL_LEADS −1. Strength WEAK / MODERATE / STRONG is an ordinal count, **not a probability**; no confidence is reported because none is calibrated. **SIGNAL** only for a lean of MODERATE+ strength outside retail-led markets (headline PROFESSIONAL PRICING INFERENCE). Other leans are OBSERVED MARKET DISAGREEMENT → PASS. |
| Evaluation | `evaluate.py` | Historical engineering evaluation with baselines and sensitivity |
| Prospective test | `prospective.py` | Implements the frozen spec (H1, H2), written before any prospective outcome |
| Site | `site.py` | Flag `V7_INTEL=on`. Upcoming cards, write-once final pregame records `v7intel_*`, prospective validation |

**Classifications:** PROFESSIONAL PRICING LEANS HOME / AWAY / OVER / UNDER, CONFLICTING EVIDENCE, INSUFFICIENT DATA,
plus **NO MATERIAL DIFFERENCE**. I added the last one because labelling fresh, agreeing prices "insufficient data"
would be false.

**Thresholds** (0.5-pt gap, 0.25-pt hold, 0.03 juice shift, MODERATE for a signal) were fixed a priori from the Tier A
definitions and never tuned.

## 3. Historical evaluation (2026, DEVELOPMENT: games already examined, not proof)
`python -m intel_v7.evaluate`. The decision is the final pregame snapshot (latest fetch ≤ kickoff − 30 min).
Bets are placed at the best observed executable price of that fetch. All CIs are 95%, bootstrapped by game.
"CLV vs consensus close" values the price at the all-operator consensus close. "CLV vs pro close" is circular for a
Pinnacle-derived signal and is never used to claim value.

Matched finals: 821 · data through 2026-11-11T01:08:18.000000Z

### Counts (final pregame snapshot)
| Sport/market | Eligible | SIGNAL | PASS | Inference | Observed disagreement | No material difference | Conflicting | Insufficient |
|---|---|---|---|---|---|---|---|---|
| nfl spread | 136 | 9 | 127 | 9 | 26 | 101 | 0 | 0 |
| nfl total | 136 | 4 | 132 | 4 | 25 | 107 | 0 | 0 |
| cfb spread | 691 | 32 | 659 | 32 | 160 | 391 | 1 | 107 |
| cfb total | 691 | 23 | 668 | 23 | 138 | 472 | 2 | 56 |
| **all** | 1654 | 68 | 1586 | | | 1071 | 3 | 163 |

Module outcomes (all eligible): divergence:INSUFFICIENT 73, divergence:MATERIAL 130, divergence:NOT_MATERIAL 1449, divergence:PROS_DISAGREE 2, key:KEY_DEFENSE 1, key:NOT_APPLICABLE 1518, key:NO_DEFENSE 135, resistance:ACTIVE_RESISTANCE 8, resistance:FOLLOWED 317, resistance:INDETERMINATE 198, resistance:NO_RETAIL_MOVE 787, resistance:PRO_LED_MOVE 343, resistance:RESISTANCE_UNCORROBORATED 1

### Signals, leans and baselines (final pregame snapshot)
| Group | n | W-L-P | ROI at best executable price [95% CI] | CLV vs consensus close [CI] | CLV vs pro close [CI] | Move toward side by close: retail [CI] / pro (pts) | Wins vs fair expectation |
|---|---|---|---|---|---|---|---|
| V7 SIGNAL (all) | 65 | 29-34-2 | -10.9% [-34.6%, +13.0%] | -1.9% [-2.3%, -1.4%] | -0.3% [-0.9%, +0.4%] | +0.08 [+0.02, +0.14] / -0.18 | -3.45 (z -0.87) |
| V7 SIGNAL nfl spread | 9 | 6-1-2 | +68.5% [+10.0%, +99.7%] | -1.1% [-1.9%, -0.0%] | +1.4% [-0.2%, +2.8%] | +0.06 [+0.01, +0.14] / -0.02 | 2.39 (z 1.81) |
| V7 SIGNAL nfl total | 4 | 1-3-0 | -51.8% [-100.0%, +44.4%] | -2.2% [-2.7%, -1.6%] | +0.3% [-0.4%, +0.8%] | +0.11 [+0.00, +0.22] / -0.02 | -1.11 (z -1.11) |
| V7 SIGNAL cfb spread | 30 | 14-16-0 | -9.6% [-48.1%, +23.2%] | -2.2% [-2.8%, -1.5%] | -0.5% [-1.6%, +0.4%] | +0.04 [-0.06, +0.14] / -0.22 | -1.36 (z -0.5) |
| V7 SIGNAL cfb total | 22 | 8-14-0 | -30.5% [-65.3%, +4.7%] | -1.7% [-2.3%, -0.9%] | -0.7% [-1.7%, +0.2%] | +0.13 [+0.04, +0.25] / -0.21 | -3.37 (z -1.44) |
| V7 SIGNAL at Pinnacle's own price | 64 | 29-32-3 | -10.9% [-35.6%, +12.8%] | -5.7% [-6.0%, -5.3%] | -4.2% [-4.6%, -3.7%] | +0.08 [+0.02, +0.13] / -0.18 | -1.99 (z -0.51) |
| Observed disagreement (lean, PASS) | 348 | 175-168-3 | -1.4% [-10.3%, +8.5%] | -1.8% [-2.0%, -1.6%] | -1.2% [-1.5%, -1.0%] | +0.08 [+0.05, +0.11] / +0.01 | 0.23 (z 0.02) |
| lean strength WEAK | 337 | 171-161-3 | -0.4% [-10.1%, +10.5%] | -1.8% [-2.0%, -1.6%] | -1.2% [-1.5%, -1.0%] | +0.08 [+0.04, +0.11] / +0.01 | 1.82 (z 0.2) |
| lean strength MODERATE | 64 | 29-33-2 | -9.4% [-32.4%, +16.3%] | -1.9% [-2.3%, -1.5%] | -0.3% [-0.9%, +0.3%] | +0.07 [+0.02, +0.12] / -0.19 | -2.91 (z -0.74) |
| lean strength STRONG | 1 | 0-1-0 | -100.0% [-100.0%, -100.0%] | +0.4% [+0.4%, +0.4%] | +1.6% [+1.6%, +1.6%] | +0.83 [+0.83, +0.83] / +0.58 | -0.53 (z -1.07) |
| lean with B ACTIVE_RESISTANCE | 7 | 1-6-0 | -72.7% [-100.0%, -18.2%] | -1.5% [-3.0%, +0.1%] | -0.1% [-1.9%, +1.9%] | +0.13 [-0.11, +0.41] / -0.02 | -2.58 (z -1.95) |
| lean with B RESISTANCE_UNCORROBORATED | 0 | - | - | - | - | - | - |
| lean with B PRO_LED_MOVE | 336 | 169-160-5 | -0.7% [-10.6%, +9.8%] | -1.8% [-2.0%, -1.7%] | -1.4% [-1.6%, -1.1%] | +0.08 [+0.04, +0.11] / +0.01 | 1.55 (z 0.17) |
| lean with C KEY_DEFENSE | 1 | 0-0-1 | - | -2.3% [-2.3%, -2.3%] | +1.0% [+1.0%, +1.0%] | +0.00 [+0.00, +0.00] / +0.10 | None (z None) |
| lean with A corroborated by both pro operators | 27 | 8-18-1 | -40.8% [-70.4%, -11.0%] | -1.7% [-2.3%, -1.0%] | -0.2% [-1.1%, +0.7%] | +0.11 [+0.01, +0.21] / -0.19 | -5.44 (z -2.14) |
| BASELINE SHOP (every game-market) | 1486 | 710-751-15 | -5.7% [-10.0%, -0.7%] | -1.7% [-1.8%, -1.6%] | -1.1% [-1.2%, -1.0%] | +0.01 [-0.00, +0.02] / -0.05 | -33.79 (z -1.77) |
| BASELINE MIRROR (retail lean on material divergence) | 122 | 58-60-4 | -2.9% [-20.5%, +12.8%] | -0.4% [-0.8%, -0.1%] | -2.2% [-2.6%, -1.8%] | -0.09 [-0.15, -0.04] / +0.18 | -0.27 (z -0.05) |
| BASELINE BOTH SIDES (average) | 2972 | 1460-1456-36 | -2.6% [-3.1%, -2.1%] | -1.8% [-1.9%, -1.8%] | -1.8% [-1.9%, -1.8%] | +0.00 [+0.00, +0.00] / +0.00 | -10.3 (z -0.38) |

### Sensitivity (rules unchanged; reported, never used to choose a rule)
| Variant | Eligible | SIGNAL | V7 SIGNAL: n, record, ROI, CLV consensus, retail move | SHOP baseline CLV consensus | MIRROR CLV consensus |
|---|---|---|---|---|---|
| horizon T-6h | 1654 | 27 | 27, 12-15-0, -14.3%, -0.5% [-1.7%, +0.6%], +0.38 | -1.8% | -2.4% |
| horizon T-24h | 1654 | 16 | 16, 11-5-0, +34.3%, -2.0% [-5.1%, +0.5%], +0.08 | -1.8% | -1.3% |
| cadence 1/2 | 1654 | 40 | 37, 15-22-0, -20.8%, -1.3% [-2.0%, -0.4%], +0.16 | -1.6% | -0.8% |
| cadence 1/3 | 1654 | 33 | 30, 15-15-0, -2.8%, -2.1% [-3.0%, -1.1%], +0.01 | -1.8% | -0.4% |
| freshness: decision fetch <= 1 h | 1654 | 57 | 57, 26-29-2, -8.4%, -1.9% [-2.4%, -1.5%], +0.07 | -1.7% | -0.5% |
| Pinnacle only (BetOnline group ignored) | 1654 | 47 | 45, 21-23-1, -7.2%, -1.9% [-2.4%, -1.4%], +0.07 | -1.7% | -0.5% |
| gap threshold 0.75 | 1654 | 17 | 16, 5-9-2, -31.3%, -1.4% [-2.3%, -0.5%], +0.15 | -1.7% | +0.8% |
| gap threshold 1.0 | 1654 | 9 | 8, 1-6-1, -72.7%, -1.6% [-2.9%, -0.1%], +0.11 | -1.7% | +3.9% |

### What the evaluation says
1. **Signals are rare by design.** 68 SIGNALs out of 1,654 eligible game-markets (4%); 1,071 show no material
   difference. Only 13 NFL SIGNALs in 10 weeks.
2. **Some price discovery, but two-way.** After a SIGNAL, retail moved slightly toward the professional side
   (+0.08 pt, CI +0.02 to +0.14), and the professional price moved further toward retail (−0.18 pt). The gap
   closes from both sides, mostly by the professionals. That matches the earlier CFB finding that Pinnacle often
   follows the market there.
3. **No betting value.**
   - SIGNAL CLV vs the consensus close is −1.9% (CI −2.3% to −1.4%), no better than the SHOP baseline (−1.7%).
   - The outcome record is 29-34-2 (ROI −10.9%, CI −35% to +13%); wins vs fair expectation −3.5 (z −0.87).
   - At Pinnacle's own price, CLV is −5.7%: there was no shopping edge either.
4. **The retail side did better at the close.** The MIRROR baseline (the retail side of material divergences) had
   −0.4% CLV vs the consensus close. At wider gaps it was positive (+0.8% at a 0.75-pt gap, n = 23; +3.9% at 1.0 pt, n = 4). This is a
   **development observation only**. It is not adopted, because adopting it would be tuning to history.
5. **NFL spread SIGNALs went 6-1-2.** That is n = 9, CLV vs consensus −1.1%. It is noise until proven prospectively.
6. **Sensitivity changes counts, not conclusions.**
   - Every variant leaves SIGNAL CLV vs consensus negative: horizon, cadence (½, ⅓), freshness (≤ 1 h),
     Pinnacle-only, and thresholds 0.75 / 1.0.
   - Thinning the collection cut SIGNALs from 68 to 33–40.
   - Active resistance appeared only 8 times at our cadence, and key-number defense once. Both are reported but
     almost never decide a card.

**Verdict:** V7 can describe professional pricing positions from real prices, and it passes most game-markets.
Nothing here shows that following them is profitable. The frozen prospective test (H1 price discovery, H2 closing
value beyond shopping) is the only way any claim can be made.

## 4. Ten real historical cards
See `docs_v4/V7_CARDS.md` (cards rebuilt exactly as of their decision time; outcomes added afterwards and labelled).

## 5. Website (development)
The NEED tab, when `V7_INTEL=on`:
1. **Professional Market NEED Signal** (prominent; "ACTUAL BOOK LIABILITY: UNKNOWN"): SIGNAL cards with every field,
   PASS cards collapsed, and the prospective record.
2. **Financial NEED**: the existing section (synthetic book), unchanged.
3. **Trading Desk Status**: the v6.1 panel when `NEED_V6=on`.

The three are never combined. With the flag off, the NEED tab text is identical to production (browser-checked). The
V7 step adds about 11 s to a build (measured locally).

## 6. Automatic refresh
- Runs inside the existing site build with no new API calls.
- Cards are recomputed from the immutable quote store, so processing is idempotent and deterministic.
- Evidence is timestamped (fetch times, change windows).
- Final pregame records are written once (`v7intel_*`, never matching the Need tracker's `need_*`); late-written
  records are excluded from validation.
- Missing or stale data → INSUFFICIENT DATA / INDETERMINATE.
- When the cadence cannot show a signal type (resistance with fewer than 2 fetches), the module returns
  INDETERMINATE rather than inventing a move.

## 7. Known limitations
1. **Real liability is unobservable.** A professional pricing lean is not a NEED.
2. **Cadence limits timing.** Median ~41 min in the last 6 h, 76 min overall; changes are known only to their
   window; "who moved first" inside a window is unknown. Resistance and key defense are therefore rarely observable.
3. **Pinnacle is missing from many fetches**, so the BetOnline group substitutes. CFB had 163 INSUFFICIENT
   game-markets.
4. **Only two professional operators**, and no Circa or other market makers in our feed.
5. **CFB normalisation is plain** (no key numbers; low confidence). Totals use no key numbers.
6. **Convergence history in production uses only the live shards** (from 26 Oct), so it starts thinner than in this
   evaluation, which used the backfill.
7. **The site shows CFB AP Top-25 games only** (the live rule). The evaluation covered every CFB game in the store.
8. **"Best executable price"** was on the board at the fetch; it may have moved before anyone could bet it.
9. **Development numbers are not validation;** the prospective sample this season will likely be too small
   (INCONCLUSIVE is a pre-declared, acceptable result).

## 8. Files (this branch vs `dev/need-engine-v6.1`)
- **New code:** `intel_v7/__init__.py`, `config.py`, `history.py`, `modules.py`, `convergence.py`, `classify.py`,
  `evaluate.py`, `prospective.py`, `audit.py`, `site.py`.
- **New tests:** `tests_v7/test_intel_v7.py`.
- **New docs:** `docs_v4/V7_MARKET_INTEL.md`, `V7_PROSPECTIVE_SPEC.md`, `V7_CARDS.md`.
- **Edited:**
  - `web_v4/live_site.py`: flag-guarded V7 step and the `v7panel` placed first in the NEED tab;
  - `.github/scripts/site_inputs.sh`: one download line for saved `v7intel_*` records;
  - `docs_v4/DEVLOG.md`.

## 9. Deployment (owner approval required; NOT done)
1. Approve merging `dev/v7-market-intel` into `v4-dev`. This includes v6.1, which stays off unless `NEED_V6=on`.
2. Approve the workflow edit to `.github/workflows/site-build.yml`, step "Build site":
   ```yaml
        env:
          V7_INTEL: ${{ vars.V7_INTEL }}
          NEED_V6: ${{ vars.NEED_V6 }}      # only if v6.1 is also approved
   ```
3. The owner adds the repository variable `V7_INTEL` = `on`. The next site build shows the panel and starts freezing
   prospective records. This is the start date of the frozen spec.
4. Evaluate with `python -m intel_v7.prospective` at 100 SIGNALs (report only), then at 300 or 2027-02-15.

## 10. Rollback
- **Instant:** remove `V7_INTEL` (or set anything other than `on`). The page returns to its previous content on the
  next build. Saved `v7intel_*` records stay; they are immutable.
- **Full:** `git revert -m 1 <merge sha>` on `v4-dev` and push (never force-push).
