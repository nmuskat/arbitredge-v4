# DRAFT: Prospective Signal Pre-Registration (NOT FROZEN; for owner review)

*Status: DRAFT. It becomes binding only when you approve it and it is committed with a timestamp and hash
**before** the first game it covers. After freezing, nothing changes; an amendment becomes a new, separately
registered test. Research only: no production, collection, site or live-signal change.*

## 1. Common rules (all signals)
* **Data:** only quotes collected by the existing live collection, with fetch time ≤ decision time. Final scores are used only for grading.
* **Normalization:** `research_v4/normalize.py` as committed at freeze (NFL validated; CFB LOW CONFIDENCE).
* **Fair price:** Pinnacle no-vig fair line at the decision fetch. If Pinnacle is absent, the BetOnline/LowVig group's median, flagged "maker proxy" (they are one operator).
* **Executable price:** best of Fanatics / BetOnline / LowVig at the decision fetch (actual point and price). If none quotes the side, the signal is recorded as NOT EXECUTABLE (counted, not graded).
* **Decision windows (fixed):** the first fetch in **[T-30h, T-18h]**, **[T-8h, T-4h]** and **[T-90m, T-45m]**. A game-market counts at most once per signal: the earliest window in which it qualifies.
* **Each signal is registered in both directions** (A = stated side, B = opposite). Both are graded identically.

## 2. Signals

| ID | Name | Trigger (all at decision fetch) | Direction A | Direction B |
|---|---|---|---|---|
| **S1** | Pinnacle-vs-recreational gap | \|Pinnacle fair − rec-median fair\| ≥ 0.5 pt, Pinnacle and ≥ 3 rec books present | Side Pinnacle rates higher than the recreational books | Opposite |
| **S2** | Active market-maker deviation | Pinnacle or the BOL group ≥ 0.5 pt from the median of all other books for ≥ 2 consecutive fetches, **and** its quote changed during the run (not stale) | `candidate_need`: the side its price rates stronger (the side it would need *if* deliberate) | Opposite |
| **S3** | Executable EV vs Pinnacle | Your best price for a side has EV ≥ +1.0% using Pinnacle's no-vig fair | That side | Opposite (control) |
| **S4** | Key-number hold, juice moving | Pinnacle holds the point on 3 or 7 (NFL spreads) while ≥ 2 other books are 0.5 off it, and Pinnacle's juice moved ≥ 10 cents in the run | Side Pinnacle's juice makes more expensive | Opposite |

**Directional record, kept for every signal instance:**
1. **observed** (what the book did);
2. **opinion** (which side its price rates stronger);
3. **candidate_need**, or **UNKNOWN** when the observation doesn't identify a direction (e.g. a stale quote; Pinnacle missing; deviation by a recreational book);
4. **identifiable** (yes/no).

S2 and S4 instances that are not identifiable are recorded as UNKNOWN and **not graded as directional bets**.

## 3. Tests and multiple-testing plan
* **Primary tests (pooled across sports and markets; 8 total):** S1, S2, S3, S4 × directions A and B.
* **Secondary (descriptive only):** each signal by sport × market, and by decision window.
* **Correction:** Holm-Bonferroni across the 8 primary tests (α = 0.05 family-wise).

## 4. Evaluation layers (each reported separately; none implies another)

| Layer | Measure | Role |
|---|---|---|
| **Expected value** | EV at decision vs Pinnacle fair | Was it +EV on the information available? |
| **Model uncertainty** | EV recomputed with a CFB sigma of ±1.5 and a NFL key-weight bootstrap; the share of instances still ≥ 0 | Is the EV robust to normalization assumptions? |
| **Execution feasibility** | Share EXECUTABLE; quote age at decision; which book; price change by the next fetch (did it vanish?) | Could you actually have bet it? |
| **Market validation** | **CLV:** EV at the Pinnacle no-vig close at the bet's own number and price; movement of the fair line toward the signal side | Necessary evidence; **not sufficient** |
| **Actual outcomes** | Win/loss/push; log loss and Brier of the signal's implied probability vs real results | Reality check |
| **Realized ROI** | At actual odds and at $115-to-win-$100 | Reported; not a gate this season |

**Baselines, same windows and pricing:** Pinnacle-only (bet toward Pinnacle's fair at your best price whenever its EV > 0); Financial NEED (live synthetic book); the existing sharp classifications (Sharp vs Public, Aligned, bigger sharp side); and a random side (seeded RNG, fixed at freeze).
**Incremental value:** S2 over S1, S4 over S1, and S1 over Pinnacle-only. Paired, same games, game-clustered CIs.

## 5. Sample sizes (from observed development variance)
* CLV SD 0.022 per bet. With 8 Holm-corrected tests, detecting a +1.0% mean CLV needs **about 60 graded instances per test**; +0.5% needs **about 240**.
* Result SD 0.96 units: +5% ROI needs **about 4,000 per test**. **Not achievable this season**, so ROI is reported but not used as a gate.

| Signal | Development rate per week | Expected time to n ≈ 60 per direction |
|---|---|---|
| S2 | ~26/wk | ~3 weeks |
| S3 | ~10/wk | ~6 weeks |
| S1 | ~7.6/wk | ~8 weeks |
| S4 | rare (NFL only); est. < 2/wk | likely not this season |

## 6. Decision rules (frozen)
* **Status after the planned n:**
  * **CANDIDATE:** Holm-significant positive CLV **and** fair-line movement toward the side **and** an EV layer robust to uncertainty **and** ≥ 70% executable **and** outcome log loss not worse than Pinnacle-only.
  * **WATCH:** positive but not significant.
  * **REJECT:** CLV ≤ 0, or the opposite direction is better.
* **No official betting recommendation follows automatically from CANDIDATE status.** A separate owner decision is required, with ROI still reported as unvalidated until about 4,000 graded bets.
* **Kill rule:** any signal whose direction-B CLV is significantly better than direction A's is re-labelled with B as the hypothesis, and that is a new test from that date.

## 7. What freezing requires (your approval)
1. Your review of thresholds (0.5 pt, +1.0% EV, 10-cent juice, windows) and the 8 primary tests.
2. Commit this file, frozen, with its hash and the commit hashes of `normalize.py` and the evaluation script, before the first covered game (e.g. TNF).
3. Evaluation runs offline from collected data. No collection, site or live-signal changes.
