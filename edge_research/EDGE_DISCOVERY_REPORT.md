# ArbitrEdge: comprehensive edge-discovery report (2026-11-11)

**Branch:** `research/edge-discovery`. The plan was committed **before** evaluation (ae2909a, 18:10:32 UTC;
`PLAN_HASH` d964e9c4…).
**Scope:** research only. Nothing is merged or deployed, no credits were spent, collection is unchanged, the
sportsbook and its bettors are untouched, the Thursday pilot is untouched, and the SIMS model is untouched.

## VERDICT: MARKET-MOVEMENT SIGNAL ONLY
- No hypothesis shows a profitable, validated advantage. None of the 8 testable primary cells passes the
  multiple-testing correction.
- The strongest lead predicts **where US books' NFL prices move**. It has **not** shown that the move is big
  enough to beat the vig, or that it predicts game results.
- **CFB:** NO EVIDENCE OF EDGE.
- **Game-outcome questions** (who is right when books disagree): INSUFFICIENT DATA in every market.
- **Real public betting:** NOT TESTABLE.

## THE STRONGEST LEAD FOR ARBITREDGE
**In the NFL, US sportsbooks drift toward Pinnacle's number. In college football they do not.**

**The pattern**
- In NFL spreads, when Pinnacle's fair line differs from the US books' median in the last 6 hours, the US books
  close about 15% of the gap per collection interval (95% CI 11%–20%). Pinnacle closes none of it (−3%, CI −9% to
  +2%). NFL totals show the same asymmetry (23% vs 2%).
- When a US book's NFL spread price was at or better than Pinnacle's no-vig fair value a day before kickoff, the
  whole market moved **toward that bet** by kickoff: +0.23 points (CI +0.03 to +0.44; 33 bets).

**Why it might exist.** Pinnacle takes large limits from professional bettors and moves on their information. US
books copy or lag it in the NFL, the most efficient and most-watched market. In college football (hundreds of
games, thinner Pinnacle limits) Pinnacle moves toward the US books just as much as they move toward it. Earlier
Tier A work found the same split (NFL books → Pinnacle slope 0.65–0.75; CFB Pinnacle → books 0.39–0.68).

**What the evidence shows**
- **It predicts market movement:**
  - yes, at the level of the gap;
  - moves inside a collection interval travel both ways (Pinnacle also follows US moves: 0.23, CI 0.04–0.42), so
    "Pinnacle moves first" cannot be established at our cadence.
- **It does not demonstrate value after the vig:**
  - a day before kickoff, closing-line value against the consensus close is **+0.0%** (CI −1.0% to +1.2%), i.e.
    break-even;
  - one hour before kickoff it is **−0.8%** (CI −1.2% to −0.4%);
  - against Pinnacle's own close it is +1.0%, but that is mostly mechanical: Pinnacle itself barely moved toward the bets
    (−0.02 pts at T − 24 h, +0.02 pts at T − 60 min; both CIs span 0).
- **Game results are not informative at this size:**
  - 16-15-2 (T − 24 h) and 18-10-3 (T − 60 min) on 31–33 bets;
  - ROI CIs span about ±30 points.
- **Only 36–39% of the flagged prices were still there at the next collection.**

**Why it deserves attention over the others**
- It is the only signal with a credible mechanism, a consistent sport split, and movement evidence that survives
  game clustering.
- It is the cheapest to test prospectively: it uses data we already collect.
- Every other candidate is negative or untestable.

**The one next step.** Approve running the frozen experiment **PX1** (`edge_research/PROSPECTIVE_PX1.md`) on the
rest of the 2026-27 NFL season. It needs one flag-guarded step in the existing site build, no API calls and no
schedule change. Judge it at the Super Bowl by its pre-written rules.

---

## 1. Research coverage and data limitations

### What exists and was verified
| Source | Status | Detail |
|---|---|---|
| Collected odds store | **Available** (read-only copy) | 1,032,792 spread/total quote rows; 10 books (Pinnacle, BetOnline, LowVig, DraftKings, FanDuel, BetMGM, Caesars, BetRivers, ESPN Bet, Fanatics); 0 recorded anomalies; data to 2026-11-11 04:28 UTC |
| Provenance: archive | Usable | Provider historical snapshots (The Odds API historical endpoint, provider timestamps), retrieved 2026-11-10. NFL 2026-09-09 → 10-27; CFB AP Top-25 only, 2026-08-26 → 10-25. Daily 16:00 UTC plus −3 h / −1 h / −10 min around kickoffs. |
| Provenance: live | Usable | Collected live from 2026-10-26/27; median fetch gap ~41 min in the last 6 h |
| Finals | Available | 821 matched (NFL from nflverse; CFB from CFBD), read-only copies of `v4-results` |
| Synthetic sportsbook (20,000 bettors, ~146k wagers) | Available, **not used as market evidence** | Synthetic; its fade/tail and NEED studies are summarized in §4 |
| Genuine Financial NEED records | Available | Graded earlier (Priority 1); no edge |
| SIMS original forecasts | **Not available here** | The verified export (226 rows) went to the owner, not this session; the local SIMS clone holds 1 forecast. Not tested. |
| Real public ticket/handle splits | **Do not exist** | None in any project store (`docs_v4/DATA_SOURCES.md`). Family D is NOT TESTABLE. Synthetic percentages were not substituted. |
| Circa, BetCRIS | **Not in our feed** | Only Pinnacle and the BetOnline/LowVig operator (one company, identical numbers in 98.6% of fetches) are professional books |

### Coverage at the decision time (kickoff − 60 min)
| Market | Games | With final | Decision + close observed | Pinnacle or BetOnline at decision | Median fetches per game |
|---|---|---|---|---|---|
| NFL spread | 136 (wk 1–10) | 136 | 136 | 136 | 43 |
| NFL total | 136 | 136 | 136 | 136 | 43 |
| CFB spread | 691 (wk 0–11) | 685 | 546 | 589 | 22 |
| CFB total | 691 | 685 | 591 | 637 | 22 |

### Main limitations
1. **Leadership resolution is our fetch interval** (~40–100 min). "Who moved first" inside an interval is
   unknowable: the provider's `last_update` is a refresh time, not a change time (Tier A).
2. **Pinnacle is missing from many fetches**, so the BetOnline group substitutes in H1–H5. PX1 requires Pinnacle
   itself.
3. **CFB fair lines use a plain (low-confidence) distribution.**
4. **All 2026 data had already been studied** (Tier A, V7, DESK-v1). Every number below is **exploratory**.
5. **The "close" is the last fetch before kickoff** (≤ 90 min before it). The pre-registered consensus close is a
   median of 9 operators, 7 of them US retail. It mostly measures where the US books ended up.

## 2. Primary hypotheses (fixed before evaluation; `edge_research/PREREGISTRATION.md`)
| ID | Family | Signal at decision time | Bet |
|---|---|---|---|
| H1 | A, disagreement | \|pro fair − US median fair\| ≥ 0.5 pt | pro side, best price |
| H2 | A, resistance | US moved ≥ 0.5 pt in the prior 1.5–12 h while pro moved ≤ 0.25 pt the same way | against the US move |
| H3 | B, price pressure | pro point unchanged, no-vig probability shifted ≥ 0.03 | the side gaining probability |
| H4 | C, leadership | consecutive-fetch gap closing (movement only) | — |
| H5a | shop at T − 60 min | a price ≥ pro fair (EV ≥ 0) | that price |
| H5b | shop at T − 24 h | as H5a, one day out | that price |

- **Primary metric:** mean CLV against the consensus close, one-sided test, game-clustered bootstrap, Holm across
  20 cells, minimum 30 bets.
- **Secondary metrics:**
  - move toward the bet;
  - directional CLV vs the opposite side;
  - outcome excess;
  - ROI;
  - archive vs live era.

## 3. Historical performance (exploratory; 95% CIs, bootstrapped by game)
| Cell | Bets | W-L-P | CLV vs consensus close | p (≤ 0) | CLV vs Pinnacle close† | Consensus move toward bet (pts) | ROI at actual price | Holm |
|---|---|---|---|---|---|---|---|---|
| H1 NFL spread | 16 | 11-3-2 | −1.3% (−1.9, −0.5) | 0.999 | +1.3% (+0.3, +2.2) | +0.13 (+0.05, +0.23) | +46.8% (+3.9, +82.2) | INSUFFICIENT |
| H1 NFL total | 8 | 4-4-0 | −1.8% | 1.0 | −0.2% | +0.12 | −2.8% | INSUFFICIENT |
| H1 CFB spread | 65 | 28-37-0 | −1.6% (−2.2, −1.0) | 1.0 | −0.2% | +0.03 | −16.5% (−40.0, +7.1) | fail |
| H1 CFB total | 44 | 17-27-0 | −1.8% (−2.5, −1.2) | 1.0 | −0.5% | +0.07 | −25.3% (−51.8, +1.5) | fail |
| H2 all cells | 28 | 13-15-0 | −2.0% (−2.7, −1.3) | 1.0 | −1.5% | **−0.16 (−0.26, −0.07)** | −10.1% | INSUFFICIENT in every cell |
| H3 all cells | 3 | 0-3-0 | — | — | — | — | — | NOT TESTABLE (3 events) |
| H5a NFL spread | 31 | 18-10-3 | −0.8% (−1.2, −0.4) | 1.0 | +1.0% (+0.4, +1.6) | +0.07 (+0.02, +0.13) | +23.6% (−8.3, +54.8) | fail |
| H5a NFL total | 13 | 7-6-0 | −1.6% | 1.0 | −0.7% | −0.04 | +4.1% | INSUFFICIENT |
| H5a CFB spread | 162 | 68-90-3 | −0.1% (−0.5, +0.3) | 0.73 | +0.6% (+0.2, +1.0) | −0.03 | −15.3% (−30.3, −0.3) | fail |
| H5a CFB total | 99 | 47-52-0 | −0.8% (−1.2, −0.3) | 1.0 | −0.1% | −0.07 | −7.6% | fail |
| **H5b NFL spread** | 33 | 16-15-2 | **+0.0% (−1.0, +1.2)** | 0.48 | +1.0% (−0.3, +2.3) | **+0.23 (+0.03, +0.44)** | +1.3% (−30.8, +33.3) | fail |
| H5b NFL total | 19 | 8-11-0 | −2.0% | 0.98 | −1.5% | −0.18 | −19.1% | INSUFFICIENT |
| H5b CFB spread | 177 | 85-87-3 | −0.6% (−1.5, +0.2) | 0.94 | −0.5% | −0.12 | −2.7% | fail |
| H5b CFB total | 98 | 40-56-0 | −0.9% (−2.1, +0.3) | 0.93 | −0.9% | −0.04 | −19.4% (−37.9, −0.9) | fail |

†CLV vs Pinnacle's close is a secondary metric added after the first look. It is partly circular for
Pinnacle-based signals: if Pinnacle doesn't move, it equals the EV at decision.

**By era:** the archive and live eras agree in sign in every cell with bets in both. Live samples are small
(2–37 bets per cell). Full tables are in `edge_research/out/results.json.gz`.

### H4 leadership (consecutive fetches; slope = share of the current gap closed in the next interval)
| Market | Window | Games / pairs | US books → pro | Pro → US books |
|---|---|---|---|---|
| NFL spread | last 6 h | 136 / 540 | **0.15 (0.11, 0.20)** | **−0.03 (−0.09, 0.02)** |
| NFL total | last 6 h | 136 / 540 | **0.23 (0.16, 0.33)** | 0.02 (−0.06, 0.09) |
| NFL spread | 6–48 h | 107 / 1,055 | 0.04 (0.02, 0.05) | 0.02 (0.00, 0.05) |
| CFB spread | last 6 h | 542 / 2,914 | 0.13 (0.08, 0.17) | 0.15 (0.10, 0.21) |
| CFB total | last 6 h | 591 / 3,165 | 0.15 (0.11, 0.18) | 0.15 (0.11, 0.20) |

**Robustness:**
- Pinnacle is not stale: its NFL spread quote was unchanged in 42% of intervals, against 50% for the US median.
- In a lagged move test (does one side's last move predict the other's next move?), moves propagate **both
  ways** in all four markets. The NFL asymmetry is about the **level** the books converge to, not about who moves
  first.

### Outcome-level information (secondary, added after the first look)
- **Test:** the actual margin or total minus the US median, regressed on (pro − US median), over all game-markets.
- **Result:** every slope's CI spans roughly −5 to +8 (e.g. NFL spread 2.0, CI −4.8 to 8.3). Squared-error
  differences between Pinnacle and the US median include 0 in all 8 market × time cells.
- **Conclusion:** on game results, nobody's number can be shown to be better at this sample size (Tier A
  reached the same answer). **INSUFFICIENT DATA**, not "no edge".

## 4. Failed or untestable hypotheses
- **H1 (pro disagreement):**
  - CLV against the consensus close is negative in every cell.
  - In CFB, the pro side went 45-64.
  - NFL spreads went 11-3-2 on 16 bets: too few to test. It overlaps V7's 6-1-2 and Tier A's 11-3-1 on the same
    games, so it is the same observation repeated, not three confirmations.
- **H2 (resistance):** only 28 events. The market kept moving **with** the US books (−0.16 pts against the bet),
  the opposite of "the pro book knows better". It fails (Tier A: 78% of apparent resistance is stale quotes).
- **H3 (price pressure):** Pinnacle almost never moves its price ≥ 0.03 in no-vig probability without moving the
  number inside 12 h (0 of 423 sampled windows; 3 qualifying events overall): NOT TESTABLE as defined.
  - An exploratory 0.015 threshold gave 81 events with CLV −1.2% to −2.1% in every cell. It fails.
- **H5 in CFB and NFL totals:** the cheapest price against the pro fair does not hold up at the close.
  Pinnacle's CFB line drifts toward the US books (CFB spread CLV vs Pinnacle falls from +1.4% at decision to
  +0.6%). Results were 68-90 and 47-52.
- **Family D (public splits):** NOT TESTABLE. No real data exists.
- **Family E:**
  - Financial NEED on genuine records: NFL 10-17-1 (CLV −4.0%), CFB 20-15 (CLV −4.4%). No edge.
  - SIMS: not available in this session.
  - Synthetic-bettor fade/tail rules: none survived out of sample (`docs_v4/V4_BETTOR_FADE_TAIL.md`).
- **Earlier studies already ruled out:** the V7 SIGNAL (65 bets, CLV −1.9%), the DESK-v1 football model (weight 0
  in all four markets), NEED v6, and the trading desk.
- **Interactions:** not run. The plan allowed them only if a primary cell passed Holm, and none did.

## 5. Ranked leads
| Rank | Lead | Evidence type | Key numbers | Main failure modes |
|---|---|---|---|---|
| 1 | **NFL: US books converge to Pinnacle (PX1)** | Market movement; exploratory | Gap closing 0.15 vs −0.03 (last 6 h); T − 24 h: 33 bets, consensus CLV +0.0% (−1.0, +1.2), move +0.23 (+0.03, +0.44); T − 60 min: 31 bets, CLV −0.8% | Movement too small to beat the vig; prices vanish (36–39% survive one fetch); ~3–4 bets/week; same games as earlier studies |
| 2 | CFB: when Pinnacle and US books disagree, the US side did better | Outcomes; exploratory, mined | Pro side 45-64 (US side 64-45); V7 "mirror" CLV −0.4% | Seen twice on the same data; z ≈ 1.8 before correction; no CLV support; not recommended |
| 3 | CFB spreads: shop vs Pinnacle at T − 60 min | CLV vs Pinnacle only | +0.6% (+0.2, +1.0) vs Pinnacle close; −0.1% vs consensus; 68-90 | Pinnacle drifts away; results negative |

Only lead 1 is defensible to pursue. None is out of sample.

## 6. Statistical reliability
- **Primary test:** 20 cells, 8 with ≥ 30 bets. The smallest primary p-value is 0.48 (H5b NFL spread). **No cell
  passes Holm; none would pass uncorrected either.**
- **Measured noise:** per-bet CLV SD ≈ 2–3%, per-bet result SD ≈ 1 unit.
- **Sample sizes needed:**
  - **CLV:** proving +1% needs ~50–70 bets; proving +0.5% needs ~200–275.
  - **ROI:** proving +5% from results alone needs ~3,800–5,000 bets, more than ArbitrEdge sees in a season.
  - **So:** closing-line value is the only feasible yardstick.
- **Disclosed deviations from the plan** (none changed a primary result):
  - Secondary metrics added after the first look: CLV vs Pinnacle close, the outcome-information regression, the
    0.015 H3 variant.
  - PX1's choice of T − 24 h as the primary arm was made after seeing H5b. That is exactly why it must be tested
    prospectively.

## 7. Recommended prospective experiment: PX1 (frozen; `edge_research/PROSPECTIVE_PX1.md`)
- **What it does:** NFL spreads; arm A at the last fetch ≥ 24 h before kickoff (primary), arm B at ≥ 60 min. A
  SIGNAL is any book's price at or better than Pinnacle's no-vig fair value; PASS otherwise.
- **Primary metric:** CLV against the consensus close.
- **Secondary metrics:** Pinnacle drift, market move, price survival, result.
- **Rules fixed now:**
  - ABANDON if arm A's consensus CLV < −0.5% or its move ≤ 0 by the Super Bowl;
  - CONTINUE into 2027 only if CLV ≥ 0 and the move's lower bound > 0;
  - VALIDATED only at ≥ 150 SIGNALs with CLV's lower bound > 0 and ≥ 50% price survival.
- **Cost:** no credits; it uses data already collected.

## 8. How it integrates into V4 (for later approval)
- `edge_research/prospective.py` is self-contained. It reads the same quote store the site build already
  downloads and writes write-once `px1_*` records, the same pattern as the V7/DESK-v1 prospective records.
- **Integration:**
  - one flag-guarded line in the site-build step records decisions (builds already run every 45 min in the
    final 6 h);
  - `odds-analyze` grades them daily;
  - a small dashboard panel can show the running CLV.
- No change to the sportsbook, its 20,000 bettors, ledgers, Financial NEED, SIMS or collection.

## 9. Recommended next action
**Approve PX1 for the rest of the NFL season and stop all other model research until it reports.**

New information that would justify more research (none is free; each needs your decision):
1. **Real, timestamped ticket and handle splits** (paid; family D cannot be tested without them).
2. **Faster collection in the final NFL hours** (the expanded tier is built but not approved). It would show who
   moves first inside today's 40–100 minute window.
3. **A true market-maker feed** (Circa or BetCRIS).
4. **The verified SIMS export**, uploaded to this repository for the SIMS-vs-market comparison.

More synthetic bettors would not help: they cannot create market information.

## Files
| File | Purpose |
|---|---|
| `edge_research/plan.py`, `PREREGISTRATION.md` | Fixed plan |
| `edge_research/run.py` | Backtest of H1–H5 and H4 (read-only on the store) |
| `edge_research/out/results.json.gz` | All cells, era splits, H4, the information test and coverage |
| `edge_research/out/bets.jsonl.gz` | 877 qualifying bets |
| `edge_research/prospective.py`, `PROSPECTIVE_PX1.md` | Frozen PX1 |
| `tests_edge_research/` | Tests |
