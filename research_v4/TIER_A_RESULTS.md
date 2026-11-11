# Tier A Research Results: Market-Maker Intelligence (development data only)

*Branch `research/tier-a`. Research only; no production code, collection, data or site changed. 2026-11-11.
Data: 507,370 normalized quotes (10 books, spreads and totals), 1,042 games, 697 with final scores; live collection
Oct 26 to Nov 11 plus the historical backfill. All 2026 data has already been examined, so everything here is
**exploratory development**. Nothing here is validated.*

## Summary
1. **Normalization works** (A1). NFL uses the validated key-number model; CFB margins are LOW CONFIDENCE, and CFB totals use a plain distribution (key weights were noise).
2. **Timestamps can't order books within a fetch** (A2). The per-book "last update" is the provider's refresh time, not a price-change time.
3. **Leadership is mostly unresolvable at our cadence** (A3): 56% of move episodes are tied. Pinnacle is present at only 61% of fetches.
4. **Price discovery differs by sport** (A5). In NFL spreads, recreational books converge toward Pinnacle. In CFB spreads, Pinnacle converges toward the recreational books.
5. **78% of "resistance" is stale quotes** (A4). Only 6.6% of resistance events allow a directional (candidate-NEED) reading.
6. **No executable edge at decision time** (A6) and **no directional edge from market-maker resistance** (both sides' CLV ≈ −vig).
7. **On actual outcomes, every price source is statistically tied** (A7).

---

## A1. Probability normalization (`research_v4/normalize.py`)
Each two-way quote → no-vig probability → implied fair mean (home margin or total), via a push-aware key-number distribution. Two books at different numbers or prices become directly comparable.

| Example (NFL) | Implied fair home margin |
|---|---|
| home −3, −110/−110 | 3.15 |
| home −3, −120/+100 (juice moved 10 cents) | 3.84 (+0.69 pts) |
| home −3.5, −110/−110 (point moved half) | 4.57 (+1.42 pts, the value of the key number 3) |

* NFL: sims_v4 key-number model, validated 2008-2025.
* CFB margins: key weights from 2026 CFB finals (3 → 1.57, 7 → 1.50, 0 → 0.11), sigma 15.7 from development residuals (n = 506). **LOW CONFIDENCE.**
* CFB totals: no key structure (fitted weights were noise), sigma 15.4. **LOW CONFIDENCE.**
* Excluded: spreads > 40 points (unreliable tails). **Bug found and fixed during this work:** the first version's grid stopped at ±45 points, which produced fake EVs on blowout CFB games.

## A2. Timestamp and freshness audit
* `market_last_update` is always 0.2-2.0 min before our fetch. **88% of its changes come with no price change.** No price ever changed without it changing. It reflects provider refresh, not trader action. **It is not used for leadership.**
* Leadership resolution = our fetch interval (median **101 min** around moves).

| Book | Coverage of event-fetches | Quote unchanged while the other books moved ≥ 0.5 pt |
|---|---|---|
| DraftKings | 96% | 21% |
| FanDuel | 85% | 28% |
| BetRivers | 78% | **34%** |
| Caesars | 73% | 8% |
| BetOnline | 72% | 11% |
| LowVig | 70% | 11% |
| ESPN Bet | 67% | 18% |
| BetMGM | 62% | 7% |
| **Pinnacle** | **61%** | **8%** |
| Fanatics | 55% | 8% |

**Pinnacle is missing from 39% of event-fetches.** It often posts later, or the provider drops it. Leadership counts are biased against it.
**BetOnline and LowVig are the same operator group**, so they are not independent market makers.

## A3. Market leadership (normalized fair line, points + juice, move ≥ 0.5 pt)
* 2,307 episodes (≥ 3 books moving the same direction).
* First observed: **TIED 1,285 (56%)**, recreational 961, Pinnacle 32, BetOnline group 29.
* Share of other books that later follow: recreational-led 0.63, Pinnacle-led 0.59.
* **Conclusion:** at our cadence, leadership by "who moved first" **cannot be established**. Pinnacle's coverage gaps and the 56% ties dominate. Day-level convergence (A5) is the better evidence.

## A5. Disagreement → subsequent movement (does the gap close, and who moves?)
Gap = Pinnacle fair line − recreational median fair line at the decision time. Regress each side's move to the close on the gap (slope 1 = full convergence; 95% CI by bootstrap over games).

| Sport / market / time | n | Rec books → Pinnacle | Pinnacle → Rec books | Gap ≥ 0.5 pt |
|---|---|---|---|---|
| NFL spreads, T-24h | 122 | **0.75 [0.52, 1.06]** | **−0.36 [−0.81, −0.05]** | 11% |
| NFL spreads, T-6h | 92 | **0.65 [0.45, 0.92]** | −0.37 [−0.63, −0.11] | 9% |
| NFL spreads, T-1h | 136 | 0.23 [0.14, 0.32] | −0.13 [−0.24, −0.00] | 13% |
| NFL totals, T-6h | 92 | 0.45 [0.08, 0.78] | 0.25 [−0.16, 0.71] | 5% |
| CFB spreads, T-24h | 460 | 0.23 [−0.09, 0.61] | **0.68 [0.31, 1.08]** | 2% |
| CFB spreads, T-1h | 436 | 0.13 [0.04, 0.22] | **0.39 [0.23, 0.55]** | 4% |
| CFB totals, T-6h | 499 | 0.60 [0.33, 0.87] | 0.22 [−0.07, 0.51] | 9% |

* **NFL spreads:** recreational books move toward Pinnacle, and Pinnacle keeps moving its own way. Pinnacle is the price leader, consistent with **H2 (informed market opinion)** at the market level.
* **CFB spreads:** Pinnacle moves toward the recreational books. In CFB, Pinnacle is **not** the leader (two-way, or recreational-led discovery).
* Disagreements of ≥ 0.5 pt are rare: 2-13% of games.

## A4. Resistance, with directional records
6,579 events (a book ≥ 0.5 pt from the median of all other books for ≥ 2 consecutive fetches).

| Observed action | Count | Hypothesis label |
|---|---|---|
| Quote unchanged (stale) | 5,123 (78%) | H5 stale-consistent |
| Held point, moved juice | 767 | active |
| Moved its number | 689 | active |
| Active **market-maker** deviation | 432 | H2/H4-consistent (direction readable) |
| Active **recreational** deviation | 1,024 | H1/H5 (direction UNKNOWN) |

Each event records separately:
1. **observed** (what it did);
2. **opinion_side** (which side its price rates stronger than the market);
3. **candidate_need** (the side it would need *if* the deviation were deliberate), or **UNKNOWN**;
4. **identifiable** (true only for active market-maker deviations: 6.6% of events).

**Development test of the candidate side vs its opposite.** First identifiable event per game-market; bet at your books' best price at the confirming fetch; CLV vs the maker-consensus close.

| Slice | Candidate side: n, CLV, record | Opposite side: CLV, record |
|---|---|---|
| All | 220, **−3.7%**, 75-70-2 | **−2.7%**, 71-72-4 |
| NFL spreads | 20, −3.9%, 8-11-1 | −0.7%, 11-7-2 |
| NFL totals | 17, −3.5%, 9-8 | −2.2%, 8-9 |
| CFB spreads | 59, −3.2%, 19-10 | −3.2%, 10-19 |
| CFB totals | 124, −3.9%, 39-41-1 | −2.8%, 42-37-2 |

Both directions lose roughly the vig against the close. **No directional information** in development data (CFB spreads 19-10 is one of eight cells, with no CLV support).

## A6. Executable EV at the decision time (T-1h), fair = Pinnacle/maker no-vig, at your books

| Slice | n (EV ≥ 0) | EV at decision | EV at close (CLV) | Record | ROI (actual odds) |
|---|---|---|---|---|---|
| NFL spreads | 15 | +0.6% | **+0.3%** | 11-3-1 | +54% |
| NFL totals | 5 | +0.5% | −1.4% | 3-2 | +17% |
| CFB spreads | 49 | +1.0% | **−0.3%** | 18-22-4 | −10% |
| CFB totals | 31 | +0.8% | −0.6% | 11-13 | −11% |

At T-1h, your books rarely beat Pinnacle's fair price (about 10 per week), and the apparent edge **doesn't survive to the close** in CFB. NFL is too small to judge. CLV, outcomes and ROI are shown separately, as required. NFL's 11-3-1 with +0.3% CLV is luck-dominated.
*(This differs from the earlier line-shop report's CFB +1.5%: that counted any time of the week, all books and first sightings. Time-of-week is a registered factor below.)*

## A7. Outcome calibration (actual results, log loss; lower is better)

| Source | NFL spreads (132) | NFL totals (134) | CFB spreads (389) | CFB totals (400) |
|---|---|---|---|---|
| Pinnacle at open | 0.6964 | 0.7058 | 0.6945 | 0.6946 |
| Pinnacle T-24h | 0.6890 | 0.6960 | 0.6936 | 0.6972 |
| Pinnacle T-1h | 0.6898 | 0.6924 | 0.6939 | 0.6950 |
| Rec median T-1h | 0.6906 | 0.6945 | 0.6933 | 0.6942 |
| Pinnacle close | 0.6909 | 0.6933 | 0.6936 | 0.6934 |
| Rec median close | 0.6912 | 0.6933 | 0.6933 | 0.6931 |

Every paired difference vs the Pinnacle close has a 95% CI that includes 0 (largest: CFB totals Pinnacle T-1h +0.0016 [−0.0003, 0.0037]). **On actual outcomes, no source is distinguishable at this sample size.** NFL opening lines are slightly worse, which is expected as information arrives.

## Observed variance (for sample sizes)
* Per-bet CLV SD **0.022**; per-bet result SD **0.96 units**.
* Detecting +1.0% mean CLV needs **n ≈ 53** (6 tests) to **69** (24 tests). +0.5% needs **210-275**.
* Detecting +5% ROI on outcomes needs **n ≈ 3,800-5,000**.
* Development signal rates per week:

  | Signal | Rate per week |
  |---|---|
  | Active market-maker deviation | ~26 |
  | Your books ≥ Pinnacle fair at T-1h | ~10 |
  | Pinnacle-vs-recreational gaps ≥ 0.5 at T-1h (all sports) | ~7.6 |
  | NFL spreads only | ~1.9 |
