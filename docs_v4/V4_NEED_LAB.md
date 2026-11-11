# V4 controlled NEED investigation (research branch `research/v4-need-lab`; nothing deployed)

## 0. Production status (verified 2026-11-11, read-only)
- **Code:** the production branch `v4-dev` is at **8c003b8**. It contains no v6/v6.1/V7 code. Neither `site-build.yml`
  nor `web_v4/live_site.py` reads `NEED_V6` or `V7_INTEL`.
- **Published site:** today's snapshot (`v4-site`, built 2026-11-11 13:14 UTC) was built from commit **8c003b8**.
  Active engine **sportsbook_v5.r2**. No v6/V7 sections.
- **Branches:** no workflow has ever run on `dev/need-engine-v6`, `dev/need-engine-v6.1` or `dev/v7-market-intel`.
- **Variables:** repository variables cannot be read through this session's GitHub proxy. That is immaterial:
  production code does not read the v6/V7 flags, so they cannot activate anything.

## 1. Method
- **Isolated lab (`lab_v4/`):**
  - an in-memory copy of `engine.advance`'s loop calling the **unchanged** production engine functions;
  - no ledger, release or production file is written.
- **Identity check:** configuration A reproduces the production engine **bet for bet**. Checked on 4 real weeks
  (8,308 / 13,654 / 6,601 / 9,139 wagers, identical ids, sides, lines, prices and stakes) and in a unit test.
- **Matched simulations:**
  - every configuration runs over the same 2026 weeks (all 2026 weeks through 10 Nov: 272 NFL and 398 CFB Top-25 spread/total
    game-markets) on the same collected quotes;
  - bankrolls carry week to week from each configuration's own settled bets;
  - the production engine's **common random numbers** mean configurations differ only by the change under study;
  - seeds 1 and 2 re-key every random draw, to measure simulation randomness.
- **Configurations** (all values fixed before any run; v6 components re-used, tested there):
  - A: original V4, unchanged.
  - B: recreational price sensitivity (v6 acceptance rule vs the market makers' fair at the same minute).
  - C: per-wager limits by sport/market with a kickoff ramp, a lower limit for respected customers, game-market
    worst-case limits (NFL $25k / CFB $15k), juice shading, and closing the heavy side beyond 1.25× the limit
    (v6 exposure grid).
  - D: theoretical overround 3.5% instead of ~4.5% (same lines).
  - E: B + C + D.
- **Grading:** each NEED side is graded at the **best observed executable price** at the decision horizon (real
  quotes). Measures:
  - CLV vs the all-operator consensus close and vs the professional close;
  - retail movement toward the side;
  - result and ROI;
  - **wins minus the wins the professional close implied** (the information test).
- **Baselines on the same game-markets:**
  - DOG/UNDER (market-only rule);
  - FAV/OVER;
  - SHOP (best EV vs the professional fair);
  - BOTH sides.
- 2026 is **exploratory development data.** Nothing below is validation. No threshold was chosen from outcomes.

## 2. Phase 1: audit of V4 (configuration A)

| Item | Finding | Verdict |
|---|---|---|
| Theoretical margin vs hold | Nominal overround 4.54% (−110/−110 retail). Hold at the closing fair price 4.13%. Realized hold 6.49%, ranging **5.79%–7.35% across seeds** of the same engine. | Margin realistic. Realized hold above theoretical is luck, not design. |
| NFL vs CFB | Realized hold NFL 5.75%, CFB 7.04% (seed 0). Theoretical about 4.1–4.4% in both. | Acceptable |
| Spread vs total | Hold at fair: spreads 4.0–4.4%, totals 3.8–4.0%. Realized hold varies ±2–4 pts across seeds. | Acceptable |
| **CFB moneyline** | **Realized hold 26–35% in every seed** (NFL ML about 7%). It is consistent, so it is not luck. Likely large recreational stakes on long-shot underdogs at retail ML prices; the cause was not isolated. | **Needs a follow-up audit** |
| Recreational customers | ROI −3% to −11%; CLV at the closing fair −3.7% to −4.5%, i.e. they pay the vig. | Realistic |
| **Sharp customers** | **Lose money:** NFL −11% ROI and −3.1% CLV; CFB −1.4% and −2.2%. Their "edge" is a ≥ 0.5-pt or price difference vs the reference books in the same stale snapshot; after paying retail juice it is negative at the close. | **Unrealistic:** respected money should beat the close |
| Price sensitivity | None. Recreational customers accept any price up to an archetype cap. | Unrealistic, but low impact (B removes only 3% of bets because V4 always prices at the market) |
| Timing and cadence | Median bet 22 h before kickoff. Recreational bets are priced from a quote with a median age of **79 min** (NFL) / 66 min (CFB), 90th percentile ~4.5 h. Sharp bets are priced from fresher quotes (~30 min). | Stale pricing is inherited from the collection cadence; unavoidable without more credits |
| Limits | $10k per wager ($3k sharp); 0.03% of recreational stakes hit the cap. No game or market limits. Worst-case exposure exceeds the v6 game limit in 0.7% (NFL) / 3.5% (CFB) of game-markets. | Limits too loose to ever bind; no kickoff ramp |
| Active exposure management | Only one mechanism: after respected action the book copies the market makers until retail moves. **25% of all bets** were taken while copying. No juice shading and no closing. | Realistic as a retail re-quote policy; no liability-based management |
| NEED over time | With NEED: 644 of 670 at T−48h, 668 of 670 at T−1h. Material: 183 → 377. The side **flips in 41%** of game-markets between T−48h and T−1h. Early NEED is mostly the favourite (only 30% underdog at T−48h); by T−1h, 60% underdog. | Dynamics exist but see §4 |
| Do preferences mechanically set NEED? | At T−1h NEED is the underdog/under side only 58% of the time, so it is not a simple function of the market. But the **same engine with a different random draw** gives the same T−1h NEED side in only **38%** of game-markets across 3 seeds (25% = coin flips). | NEED direction is mostly **simulation randomness**: which large customers happen to arrive and how much they stake |

## 3. Phase 2 and 4: the five configurations (means over 3 seeds; full tables in `V4_NEED_LAB_TABLES.md`)

| Config | Handle | Nominal margin | Hold at closing fair | Realized hold [seed range] | Seed agreement on T−1h NEED | Bets at market makers' price | Risk actions (seed 0) |
|---|---|---|---|---|---|---|---|
| A original | $16.21M | 4.54% | 4.13% | 6.49% [5.79, 7.35] | 38% | 25% | — |
| B price-sensitive | $15.78M | 4.53% | 4.12% | 6.38% [5.55, 7.85] | 41% | 25% | 4,242 price refusals |
| C limits/risk | $14.65M | 4.48% | 3.65% | 4.51% [3.64, 5.33] | 40% | 26% | 357 shades, 144 closes |
| D margin 3.5% | $16.64M | 3.77% | 3.41% | 5.56% [4.48, 6.62] | 40% | 33% | — |
| E = B+C+D | $14.83M | 3.74% | 2.89% | 4.18% [3.42, 4.80] | 43% | 33% | 349 shades, 113 closes |

**NEED at T−1h, graded at the best observed price.** Ranges are across the 3 seeds; n = 640 game-markets.

| Config | ROI | CLV vs consensus close | Wins vs fair expectation |
|---|---|---|---|
| A | −1.5% to +1.2% | −1.9% | +1.0 to +9.8 |
| B | −4.0% to +2.0% | −1.8 to −1.9% | −7.1 to +12.2 |
| C | −2.8% to +2.3% | −1.8 to −2.0% | −2.7 to +12.9 |
| D | −0.4% to +2.5% | −1.8 to −1.9% | +4.7 to +13.5 |
| E | −2.6% to +5.0% | −1.8 to −1.9% | −2.1 to +21.4 (z 1.7) |
| BASELINE BOTH sides | −2.6% | −1.8% | −4.6 |
| BASELINE SHOP | −1.5% | −1.7% | −0.7 |
| BASELINE FAV/OVER | +1.1% | −1.8% | +10.5 |
| BASELINE DOG/UNDER | −6.4% | −1.9% | −15.1 |

**Where the differences come from:**
- **Hold at fair** changes are **real and mechanical**. D lowers the price charged. C's shading moves value to the
  other side, and sharps take it: NFL sharp CLV goes from −3.1% to −0.9%. B barely changes anything.
- **Realized hold and every win/loss figure** move about as much **between seeds** as between configurations.
  That is simulation randomness on one season of shared outcomes.
- **NEED's closing value is the same as betting both sides in every configuration.** No configuration made NEED
  carry market information. Material NEED and the recreational-only and respected-only NEEDs are no different.
- **Lead/lag:** NEED changes from T−24h to T−6h agree with the subsequent consensus move only **46–48%** of the
  time (CI about ±6 pts), for recreational and respected components alike. NEED does **not** precede market movement.

## 4. Phase 3: NEED patterns examined (all exploratory)
- **Direction:** see §2. NEED is not a deterministic function of market features, and it is also not reproducible
  across seeds.
- **Magnitude:** "material" NEED (swing ≥ max($5k, 5% of handle), a priori) performs like all NEED.
- **Persistence and change:** 41% of game-markets flip; NEED changes do not lead the market.
- **NFL vs CFB, spreads vs totals:** NFL spreads were positive in all 15 runs: wins vs fair +1.0 to +12.7 on the
  same 136 game-markets, z up to 2.2 (C/E seed 0). But:
  - CLV was −1.4% to −1.7%, identical to betting both sides, so the prices carried no edge;
  - all runs share the same outcomes;
  - it is one of dozens of cells.
  NFL totals and CFB were negative or flat.
- **NEED disagreeing with the market-only underdog/under rule:** positive in all runs, but that subset **is**
  favourites/overs, which did better in 2026 at T−1h prices regardless of NEED. NEED's selection within it adds
  about 2–5 wins per 100 games, within noise.
- **Respected vs recreational drivers:** neither component's NEED carries information (wins vs fair −8 to +2,
  CLV ≈ baseline).

## 5. Recommendation
1. **Keep unchanged:**
   - the production engine (sportsbook_v5.r2) and the population, arrivals, preferences and staking;
   - settlement and the Financial NEED computation;
   - the ~4.5% margin;
   - the respected-action re-quote policy (it is the book's only, and realistic, exposure-management behaviour).
2. **Justified small improvements** (realism, not performance; each needs your approval and a new engine version):
   - **(a) Per-wager limits with a kickoff ramp and lower limits for respected customers** (configuration C's caps).
     Real books do this, and V4's $10k/$3k limits never bind. The game-limit shading/closing is optional: realistic,
     but it lowers theoretical hold (to 3.65%) by giving value to sharps.
   - **(b) Show "hold at fair" next to realized hold** on the site, so luck is not read as performance (a 1.5-pt
     seed-to-seed swing is normal).
   - **(c) Audit the CFB moneyline 26–35% realized hold** before trusting moneyline numbers.
   - **(d) Re-examine the SHARP archetype.** It loses money, which no respected-customer model should. This is a
     model defect to investigate. I am not proposing a fix here: any fix would be a new design and would need its own
     test.
   - **Not justified now:** price sensitivity (B). It is realistic but immaterial while V4 always prices at the market.
3. **Lowering the margin is not justified.** Retail books charge about 4.5% on these markets. A 3.5% book raises
   handle 3% and sharp activity (25% → 33% of bets at makers' prices), lowers theoretical hold to 3.4%, and gives NEED
   no information.
4. **Preregistered test:** at most **one**, and cheap. "NFL spread Financial NEED (production, unchanged) at the last
   pre-kickoff Need-tracker snapshot beats the professional-close expectation."
   - It can use the Need-tracker records production already freezes, with no code or collection change.
   - Primary measure: wins minus expected wins at the professional close. Secondary: CLV vs the consensus close.
   - Expectation: null (CLV showed no edge). A draft is in §6, **not frozen**; I will freeze it on your approval.
   - No other pattern warrants a test.
5. **Reusable v6/V7 work:**
   - Yes: v6's exposure grid, limit/kickoff-ramp/shading logic and normaliser (used here as configuration C); V7's
     point-in-time market history, best-executable-price and outcome measures (used here as the measuring stick).
   - No: v6's trading desk and V7's signals as decision layers. Neither showed value.
6. **Keep the original V4 as production.** None of the configurations makes Financial NEED informative. B–E change
   realism, not information, and two of the most visible differences (realized hold, NFL spread wins) are mostly
   randomness.

## 6. Draft prospective specification (NOT frozen; needs owner approval)
- **Population:** every NFL regular-season and playoff spread game-market from the first Need-tracker snapshot after
  approval to the end of the 2026–27 season. Use the production Need tracker's last pre-kickoff record (engine and
  rules as deployed; no changes during the test).
- **Selection:** the recorded Financial NEED side (all, and the tracker's "qualified" subset as a secondary group).
- **Price:** the best observed executable price in the same collection run as the record.
- **Primary:** wins minus expected wins at the professional close (game-clustered; one-sided 95% lower bound > 0).
- **Secondary:** CLV vs the all-operator consensus close (lower bound > 0), and ROI (descriptive).
- **Expected sample:** about 14 games a week, about 120 by season end. INCONCLUSIVE is the likely outcome; no claim
  without both primary and secondary passing.

## 7. Files (branch `research/v4-need-lab`, from `dev/v7-market-intel`; never to be merged)
- **New code:** `lab_v4/__init__.py`, `config.py`, `runner.py`, `season.py`, `measure.py`, `experiment.py`,
  `analyze.py`, `audit.py`.
- **New tests:** `tests_lab_v4/test_lab.py`.
- **New docs:** `docs_v4/V4_NEED_LAB.md`, `docs_v4/V4_NEED_LAB_TABLES.md`; `docs_v4/DEVLOG.md` entry.
- No production file, workflow, variable, collection schedule or ledger was changed. No API credits were used.

Reproduce:
```
python -m lab_v4.experiment --market-db market.db --rankings DIR --results DIR --out OUT
python -m lab_v4.analyze --dir OUT --md tables.md
python -m lab_v4.audit --market-db market.db --rankings DIR --results DIR --out audit.json
```
