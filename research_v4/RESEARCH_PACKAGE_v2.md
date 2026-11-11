# ArbitrEdge V4: Research Package v2 (Tier A corrections, hypotheses, realism, architecture, roadmap)

*Branch `research/tier-a`. Research only: no production code, live synthetic bettors, sportsbook calculations,
NEED selections, collection, database structures or website behaviour changed. No API spend. Nothing frozen.
2026-11-11.*

Direction (owner, binding): ArbitrEdge is a **realistic synthetic sportsbook with a professional-style trading desk**, not a Pinnacle-following model. The synthetic bettor population, wagers, bankrolls, liability, settlement, Financial NEED and replay are **preserved**. Three NEED measurements stay separate: **Financial NEED**, **Market-Maker Inference**, **Trading Desk NEED**.

---

## 1. Corrected Tier A findings and data audit

### 1.1 Reconciliation: 432 → 261 → 220 → 147

| Step | Count | Why |
|---|---|---|
| Identifiable active market-maker deviation events | 432 | Pinnacle 139, BetOnline 147, LowVig 146 (repeats across fetches) |
| Distinct game-markets (first event per game-market) | **261** | BetOnline and LowVig are **one operator group** and almost always fire together, so they're not independent |
| − no observed close (no fetch ≤ 2 h before kickoff) | −28 | collection gap |
| − confirming fetch at or after the close fetch | −13 | signal too late to act |
| **Signal with a quote at your books** | **220** | |
| − no final score | −73 | **all CFB**: FCS opponents or school-name mismatches in our results matcher (fixable in research) |
| **Graded** | **147** | |

**Independence:** the 220 game-markets come from **199 games** (21 games contribute both spread and total). Design effect ≈ 1.1. All sample sizes below are inflated by 10%.

### 1.2 Execution realism (A6 executable bets, n = 100)
* **50% of the prices we recorded had changed by our next fetch** (median about 100 min later), and 50% were unchanged. A price seen at our fetch is **not** guaranteed to be bettable when you act.
* Your books' availability per event-fetch: Fanatics 55%, BetOnline 72%, LowVig 70%.
* Limits: **unknown** (no data).
* **Correction adopted:** a price counts as **PERSISTENT-EXECUTABLE** only if the same or better price is still quoted at the next fetch. CLV is reported for both "seen" and "persistent" prices. The gap between them is the execution haircut.

### 1.3 Corrections to the first Tier A report
1. **Directional schema.** Every behaviour record now carries **four separate fields**:
   * `observed_action`: what the book did;
   * `pricing_opinion_side`: which side its price rates stronger than the market;
   * `candidate_need_side`: the outcome it might want to need **only if** the behaviour is deliberate, otherwise **UNKNOWN**;
   * `value_side`: which side is +EV at your books.

   These are never equated. In particular, the **more expensive side is not assumed to be the desired outcome**. Draft S4 did assume that, and it has been withdrawn (see §3).
2. **S3 is line shopping, not positioning.** It moves to its own family (§3) and is never cited as evidence of sportsbook intent.
3. **NFL vs CFB differ** (A5). NFL spreads: recreational books converge toward Pinnacle (slope 0.75 from T-24h; Pinnacle moves away from them, −0.36). CFB spreads: Pinnacle converges toward the recreational books (0.68). All hypotheses are now **sport- and market-specific**.
4. **Unchanged findings:**
   * leadership is unresolvable at our cadence (56% tied);
   * 78% of resistance events are stale quotes;
   * the candidate-need side vs its opposite showed no CLV difference in development (−3.7% vs −2.7%);
   * no price source beats another on actual outcomes at this sample size.

### 1.4 Statistical requirements (observed variance, clustering, multiple testing)
* Per-bet CLV SD **0.022**; per-bet result SD **0.96 units**; design effect **1.1**.
* Positioning family = 18 tests (§3), Holm-corrected:
  * **about 70 graded instances per test** to detect +1.0% mean CLV;
  * about 290 for +0.5%;
  * about 4,000+ for +5% ROI on results.
* Rates measured on the **live-cadence period only** (2.15 weeks); rows are sport/market:

| Signal | NFL spreads | NFL totals | CFB spreads | CFB totals |
|---|---|---|---|---|
| S1 Pinnacle-vs-recreational gap | 3.3/wk (~21 wks) | 0.5/wk (never) | 3.3/wk (~21 wks) | 9.8/wk (~7 wks) |
| S2 active market-maker deviation | 6.5/wk (~11 wks) | 3.3/wk (~21 wks) | 10.7/wk (~7 wks) | 25.1/wk (~3 wks) |
| S3 your books +1% EV vs Pinnacle | ~0 | ~0 | 2.3/wk (~20 wks) | 1.4/wk (~34 wks) |

**Realistic this season** (about 8 NFL and about 6 CFB weeks left): S2 in CFB totals, CFB spreads and (borderline) NFL spreads, plus S1 in CFB totals. Everything else needs next season or denser data.

---

## 2. Professional sportsbook practices: documented vs assumed

| Topic | Documented | Source | What we can observe in odds |
|---|---|---|---|
| Books take positions rather than balancing, and shade prices toward bettor biases | Academic (NFL data) | Levitt 2004 ([PDF](https://www.stat.berkeley.edu/%7Ealdous/157/Papers/Levitt_Gambling_2004.pdf)) | Not liability itself. Only price shading relative to other books. |
| Sharp action is treated as price information; deliberately off openers; two-way action settles the number | Press/interviews | [Review-Journal](https://www.reviewjournal.com/?p=2160451) | Early volatility, then convergence (H1) |
| Posted limits, taken up to the limit; limits raised once respected two-way action confirms a number | Press/interviews | [RJ Q&A](https://www.reviewjournal.com/?p=3535607); [Covers](https://www.covers.com/guides/sharps-vs-sharps-five-key-lessons-from-sports-betting-pros) | **Limits are not observable** in our data |
| Openers carry low limits that rise as the market matures | Industry guides (secondary) | [FTN](https://ftnfantasy.com/?p=37175); [Betting limits glossary](https://betting-calculators-com.pages.dev/glossary/betting-limits) | Implies early moves are cheaper information; observable only as price volatility |
| 3 and 7 are the key NFL margins; half-points there are worth extra juice | Industry guides | [BetMGM](https://sports.betmgm.com/en/blog/key-numbers-in-sports-betting-bm06/); [TheLines](https://www.thelines.com/betting/nfl/key-numbers/) | Juice-only moves at 3/7 (now normalized in A1) |
| Balance while discovering the price, then book hard to the side they want to need; a big decision = 3-5 limit bets; hold 3-5%, more on sides | **Firsthand**, unverified | Circa manager via owner | Only indirectly: persistence of a held number after convergence |
| Exact limits, customer ratings, liability tolerance, internal intent | **Unknown** (proprietary) | — | **Not observable. Reported UNKNOWN.** |

---

## 3. Revised market-maker positioning hypotheses (sport- and market-specific)

Each hypothesis states the observable action, the pricing-opinion reading, the candidate-need reading (or UNKNOWN), and what would falsify it.

| ID | Sport / market | Observable | Pricing opinion | Candidate NEED | Prediction to test | Falsified if |
|---|---|---|---|---|---|---|
| **P1** | NFL spreads | Pinnacle ≥ 0.5 pt from the recreational median (T-24h/T-6h/T-1h) | Pinnacle's side | UNKNOWN (an opinion gap is not exposure) | The recreational line moves toward Pinnacle; betting Pinnacle's side at your books gives +CLV | CLV ≤ 0, or the reverse side does better |
| **P2** | CFB spreads | Same gap | Pinnacle's side | UNKNOWN | **Reverse of P1:** Pinnacle converges to the recreational books, so the recreational side has +CLV | Recreational side CLV ≤ 0 |
| **P3** | CFB totals / NFL totals | Same gap | Pinnacle's side | UNKNOWN | Two-way convergence; direction left open (both registered) | Neither direction > 0 |
| **P4** | All, per sport/market | **Active** maker deviation ≥ 2 fetches (quote changed during the run) | The side the deviating maker rates stronger | **Same side, labelled "if deliberate"; UNKNOWN for the stale/recreational cases** | The candidate side beats its opposite on CLV | Opposite ≥ candidate |
| **P5** | NFL spreads only | Pinnacle holds 3 or 7 while ≥ 2 books are 0.5 off; juice moves ≥ 10 cents | Normalized fair line from the juice (A1) | **UNKNOWN by default.** A juice move is compatible with defending, balancing or opinion. | Exploratory: does the normalized fair-line side predict CLV? | — (descriptive until n allows) |
| **P6** | All | Deviation **late** (T-6h → T-1h) vs **early** (T-24h) | — | — | Late, higher-limit deviations carry more information than early ones (limit-maturity hypothesis) | Late ≤ early |

---

## 4. Revised prospective pre-registration

See `PREREGISTRATION_DRAFT_v2.md` (**DRAFT, not frozen**). Changes from v1:
* Two families: **Positioning (P1-P6)** and **Line shopping (L1)**.
* Sport/market-specific tests.
* Persistent-executable pricing.
* Fixed CFB result matching before freeze.
* Both directions for every directional test.
* Sample-size and feasibility table.
* No automatic recommendation.

---

## 5. Recommendations for synthetic bettor realism
*(Preserve everything; implement only as a **new versioned research engine** replayed side by side with the live v5 engine on identical data. Nothing live changes.)*

| # | Change | Why (evidence) | Validation check (never tuned on game results) |
|---|---|---|---|
| R1 | **Cadence-independent sharp reaction:** sharps react to real market-maker moves at the move's time, with a latency distribution, not at our fetch times | Sharp profit flipped sign between replay and live cadence | Sharp CLV > 0 on ≥ 60% of bets under **both** cadences |
| R2 | **Customer price sensitivity:** each customer has a personal valuation (from public information, bias and noise) and bets only when the posted price beats it by their threshold | Today only sharps and one small archetype compare prices | Demand falls as the posted price worsens, per archetype |
| R3 | **Limits:** per-customer and per-game limits rising toward kickoff; respected customers capped; large bets split | Documented practice; the manager's "3-5 limit bets" | Top-5-bet share of a game's liability within a stated range |
| R4 | **Respected-customer tagging:** a small group whose bets the desk treats as information | Documented Circa practice | The desk moves more after respected bets than after others |
| R5 | **Information arrival:** NFL injury reports (nflverse, free) shift demand when published; no CFB equivalent | Customers react to news | Demand shifts follow the timestamps; no look-ahead |
| R6 | **Arrival timing:** weekday/weekend intensity; public money late, sharp money early and late | Market practice; our cadence artifacts | Arrival curves stated as assumptions |
| R7 | **Moneyline stake audit** | ML hold 13-15% with about 4.5% overround | Stake distribution by price band |
| R8 | **Market-specific mix and margins** (sides hold more than totals) | Manager: hold higher on sides | Theoretical hold 3-5%, sides > totals (price-only check) |

**Explicitly prohibited:** tuning any parameter to a realised win rate, realised hold or sharp profit; using final scores in any customer or desk decision.

---

## 6. Integration architecture (research engine "v6", parallel to the live v5)

```
every decision tick t (only data ≤ t):
  1. MARKET INTELLIGENCE   normalized prices (A1) → leadership/resistance/gaps → Market-Maker Inference records
  2. DESK PRICING          fair price + uncertainty (market-anchored; validated adjustments only)
  3. DESK QUOTE            posted line/juice + limits (desk policy, versioned and frozen)
  4. CUSTOMERS             synthetic population arrives; each compares the posted price with its valuation and limits; wagers recorded
  5. EXPOSURE              liability by outcome → FINANCIAL NEED (existing definition, unchanged)
  6. DESK DECISION         BALANCE | MOVE | DEFEND | ACCEPT EXPOSURE | NO POSITION, with a written rationale
                           → TRADING DESK NEED (or NONE) + reasons
  7. RECOMMENDATION        your books' executable prices vs desk fair vs market-maker fair → BET (exact bet, book,
                           minimum odds, probability ± uncertainty, expected ROI, validation status) or PASS
```

**Separation rules (enforced in schema):**
* Three NEED fields with distinct names, sources and validation records.
* **Trading Desk NEED requires an independent, validated reason** (not Pinnacle's price alone). Without one, it is NONE.
* Final scores are used only by grading code, which the engine never imports.

---

## 7. Prioritized roadmap

| Order | Work | Data needed | Approval |
|---|---|---|---|
| **1** | Finish the Tier A audit items in research: FCS-aware CFB result matching; persistent-executable metric; signal-overlap table; directional schema in code | existing | covered (research branch) |
| **2** | Development-only analyses of P1-P6 on 2026 data (labelled exploratory) | existing | covered |
| **3** | Owner review, then **freeze pre-registration v2** before the first covered game | existing | **Checkpoint A** |
| **4** | Offline prospective evaluation from live collection, weekly report | existing collection | covered by A |
| **5** | v6 research engine: R1-R8 realism + desk policy prototype; side-by-side replay with v5 | existing | **Checkpoint B** (design review) |
| **6** | NFL injury-report ingestion (free) | nflverse | **Checkpoint C** (engineering only) |
| **7** | Circa (provider check, or a paid provider), denser polling bursts, earlier-season history | new data / credits / money | **Checkpoint D** (spend) |
| **8** | Production: desk outputs, three NEEDs and recommendations on the site | validated results | **Checkpoint E** (deploy) |

## 8. Approval checkpoints (nothing crosses one without your explicit "yes")
* **A:** freeze pre-registration v2 (docs only).
* **B:** v6 engine and desk policy design (research only).
* **C:** new free data ingestion.
* **D:** any spend (paid provider, credits).
* **E:** any production change or deploy.
