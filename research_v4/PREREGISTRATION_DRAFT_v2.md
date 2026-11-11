# DRAFT v2: Prospective Pre-Registration (NOT FROZEN; for owner approval at Checkpoint A)

*Supersedes DRAFT v1. Binding only after owner approval and a timestamped commit (file hash + code hashes) before
the first covered game. Research only.*

## 1. Common rules
* **Inputs:** live collection quotes with fetch time ≤ decision time; normalization `research_v4/normalize.py` at the frozen hash (NFL validated; CFB LOW CONFIDENCE).
* **Decision windows:** first fetch in [T-30h, T-18h], [T-8h, T-4h], [T-90m, T-45m]. One instance per signal per game-market (the earliest qualifying window). The window is recorded for P6.
* **Fair price:** Pinnacle no-vig fair line. If Pinnacle is absent, the instance is **UNKNOWN** (not graded; the BetOnline group is not a substitute for Pinnacle, being a single operator).
* **Execution:**
  * **SEEN** = best of Fanatics/BetOnline/LowVig at the decision fetch;
  * **PERSISTENT** = the same or better price still quoted at the next fetch.

  Primary grading uses PERSISTENT. SEEN is reported as well, and the difference is the execution haircut.
* **Results:** must pass the FCS-aware CFB matcher (fixed before freeze). Unmatched games are listed and excluded symmetrically from both directions.
* **Overlap:** an instance appearing in several signals is graded in each; incremental-value analysis uses paired games only.

## 2. Family P: positioning research (Holm-corrected within the family; 18 tests)

| Test | Sport / market | Trigger | Direction A | Direction B |
|---|---|---|---|---|
| P1-A/B | NFL spreads | \|Pinnacle − rec median\| ≥ 0.5 pt | Pinnacle's side | Recreational side |
| P2-A/B | CFB spreads | same | **Recreational side** (hypothesis: Pinnacle converges to the rec books) | Pinnacle's side |
| P3a-A/B | NFL totals | same | Pinnacle's side | Recreational side |
| P3b-A/B | CFB totals | same | Pinnacle's side | Recreational side |
| P4-A/B (×4 sport/market) | each | Active market-maker deviation ≥ 0.5 pt for ≥ 2 fetches, quote changed during the run (Pinnacle-only deviations; BetOnline group reported separately, not tested) | `pricing_opinion_side` = `candidate_need_side` *(if deliberate)* | Opposite |
| P5 | NFL spreads | Pinnacle holds 3/7, ≥ 2 books 0.5 off, Pinnacle juice moved ≥ 10 cents | **Descriptive only**: normalized-fair-line side; `candidate_need_side` = UNKNOWN | — |
| P6 | all | P1-P4 instances split by window | **Descriptive**: late vs early CLV | — |

**Counted tests:** P1, P2, P3a, P3b, plus P4 × 4 sport/markets = 8 signal cells × 2 directions = **16**, plus a pooled P4 in both directions = **18**.

## 3. Family L: line shopping (separate; Holm within the family; 4 tests)
| Test | Trigger | Side |
|---|---|---|
| L1 (×4 sport/markets) | Your PERSISTENT price has EV ≥ +1.0% vs Pinnacle's no-vig fair | that side (the opposite side is a descriptive control) |

**L1 is never cited as evidence of sportsbook positioning.**

## 4. Record per instance
`observed_action`, `pricing_opinion_side`, `candidate_need_side` (or UNKNOWN), `value_side`, window, book, SEEN and PERSISTENT price, EV at decision, uncertainty-adjusted EV (CFB sigma ±1.5; NFL key-weight bootstrap), CLV vs Pinnacle no-vig close, fair-line movement to the close, result, P&L (actual odds and $115-to-win-$100), Financial NEED and existing sharp classification at the same time (baselines).

## 5. Evaluation layers (separate; none implies another)
EV · uncertainty robustness · execution feasibility (PERSISTENT share, haircut) · CLV · fair-line movement · actual outcomes (log loss/Brier) · realized ROI.

**Baselines (same instances):** Pinnacle-only, Financial NEED, existing sharp classes, seeded random side.

## 6. Sample sizes and feasibility (observed CLV SD 0.022, design effect 1.1)
* About **70 graded instances per test** to detect +1.0% CLV after the Holm correction (18 tests); about 290 for +0.5%. ROI needs about 4,000+ (**not a gate**).
* Expected this season: P4 in CFB totals (~3 wks), CFB spreads (~7 wks), NFL spreads (~11 wks, borderline); P3b CFB totals (~7 wks).
* Other cells will likely be **inconclusive until next season**, and will be reported as such.

## 7. Decision rules
* **CANDIDATE:** Holm-significant +CLV at PERSISTENT prices, **and** fair-line movement toward the side, **and** uncertainty-robust EV, **and** PERSISTENT share ≥ 60%, **and** outcome log loss not worse than Pinnacle-only.
* **WATCH:** positive but not significant.
* **REJECT:** CLV ≤ 0, or direction B better.
* **CANDIDATE never auto-creates a recommendation.** It needs a separate owner decision. ROI is reported as unvalidated.
* **Re-labelling:** if direction B is significantly better, B becomes a new hypothesis tested only from that date.

## 8. Pre-freeze checklist
1. FCS-aware CFB result matching fixed and unit-tested.
2. PERSISTENT-execution code and the directional record fields implemented and unit-tested.
3. Evaluation script and normalization committed; hashes recorded here.
4. Owner approval (Checkpoint A).
