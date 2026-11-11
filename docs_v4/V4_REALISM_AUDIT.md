# ArbitrEdge V4: synthetic sportsbook realism audit and corrections (research branch `research/v4-realism`)

Nothing here is deployed. All corrections are in development behind versioned research configurations. The
production engine is untouched.

## 1. Verified production state (2026-11-11, read-only)
- **Code:** the production branch `v4-dev` is at **8c003b8**, `ENGINE_VERSION = "sportsbook_v5.r2"`.
- **Published site:** the snapshot was built from **8c003b8**. Active engine sportsbook_v5.r2; no v6/V7 sections.
- **Production ledger** (releases `v4-book`, `v4-settlement`):
  - 277 interval files, 55,229 wagers in 10 runs (6 sportsbook_v5.r2; earlier engine versions kept as superseded);
  - 52,338 settlement records.
- **Unchanged:** no production file, data, variable, schedule or ledger was changed, and no API credits were used.

## 2. Wager-ledger reconciliation
Every wager was re-settled with an independent implementation of the payout rules (`lab_v4/reconcile.py`, written
separately from `sportsbook_v4.settlement`). Two ledgers were checked:
- **Replay ledger** (production engine re-run over NFL weeks 1–9 and CFB weeks 0–10; bet-for-bet identical to
  production by test): **146,205 wagers, 0 unsettled, 0 result mismatches, 0 P&L mismatches.** It reproduces every
  quoted figure:
  - NFL: 82,958 wagers, $9,029,378 handle, book +$519,247.79 (5.75%);
  - CFB: 63,247 wagers, $7,058,456 handle, book +$496,620.03 (7.04%);
  - combined: +$1,015,867.81 (6.31%);
  - sharps: NFL −11.0%, CFB −1.4%;
  - CFB moneyline: 7,522 wagers, $808,672 handle, 26.2%.
- **Production settlement ledger** (live weeks from 27 Oct): **52,338 records, 0 missing wagers, 0 term mismatches
  (stake, price, side, market), 0 result mismatches, 0 cent differences.**
- All 14 research ledgers built for this audit also reconcile exactly.
- **No accounting bug was found.** American-odds payouts, pushes and moneyline settlement are correct.

## 3. CFB moneyline 26.2%: root cause (no bug)
**Expected hold depends on how the vig is removed** from the market makers' closing moneyline:

| De-vig method | Expected hold at prices charged |
|---|---|
| Proportional | 1.5% |
| Additive | 6.7% |
| Power (corrects the long-shot bias) | 10.5% |

**Game-level variance is large.**
- The game-level SD of the book's moneyline result is **14.9% of handle** (power), because one long-shot win pays
  up to 30-to-1.
- The realized 26.2% is **z ≈ 1.0** above expectation under every method.
- 10 games account for 27.5% of the absolute result.

**By odds bucket** (wagers, record, fair win rate):
- +1,000 or longer: 1,200 wagers, **9–1,191**, fair win rate about 5% (proportional); 17% of handle.
- +500 to +999: 674 wagers, 36–638, about 12% expected.
- Across the **53 games** where a +1,000 long shot was bet: **1 won**, against 1.4 (power) to 2.2 (proportional)
  expected.

**The mechanism is customer selection, plus no "max win" limit:**
1. Recreational side choice uses only the **sign** of the line (favourite or not), never its size
   (`engine._recreational_side`).
2. On moneylines, the favourite side is then blocked by each archetype's `ml_max_implied_prob` (about −400 to −570),
   while the underdog side has no limit. On lopsided CFB games, only long-shot bets get through.
3. Stakes ignore the odds, so a high-stakes customer risks the same $ at +3000 as at −110. One potential payout was
   **$269,850**. Real books cap how much one bet can win.

**Correction (RISK MANAGEMENT IMPROVEMENT): max-win limit** (`runner.max_win_cap`).
- A single bet may not win more than V4's existing per-wager limit ($10,000; sharps $3,000).
- It affected only **9 wagers** ($10,943 of stake).
- CFB moneyline variance falls sharply: largest potential win $269,850 → $10,000; SD of hold 14.9% → 8.4%;
  long-shot share of CFB moneyline handle 24.6% → 16%.
- Realized CFB moneyline hold stays high (15–26% across seeds; z 0.8–2.0). That is outcome variance around an
  expected hold of about 8%.

**Not changed (OPTIONAL RESEARCH ASSUMPTION):** making moneyline side choice sensitive to the size of the price would
need an unsupported assumption about how recreational customers weigh long shots.

## 4. Sharp customers: root cause (CUSTOMER BEHAVIOUR CORRECTION)
The V4 rule (`engine._sharp_pick`) bets when the book's number is ≥ 0.5 pt better than the reference books' number,
or its price ≥ 1.5 percentage points cheaper at the same number. **It ignores the juice and key-number value when
comparing numbers.** Measured against the market makers' no-vig fair at the same minute (information the customer
had):

| Sharp bets | Expected value at decision | Share +EV | Closing value |
|---|---|---|---|
| NFL spread, "points edge" | −3.75% | 5% | −4.65% |
| Totals, "points edge" | −1.8% (NFL) / −2.4% (CFB) | 17–18% | about the same |
| All sharp bets | −2.2% (NFL) / −1.85% (CFB) | 16–19% | −2.9% / −3.1% |

**Their losses are not bad luck: they were buying worse-than-fair prices by construction.**

**Correction:** a sharp bets only when the book's price is **+EV against the market makers' no-vig fair at the same
minute** (`runner.sharp_pick_ev`).
- The threshold is V4's own `edge_threshold_prob` = 1.5%, now applied to EV.
- Spreads and totals use the push- and key-number-aware normaliser.
- Moneyline EV must clear the threshold under **both** proportional and power de-vig.
- Staking keeps V4's edge-unit formula.
- No later information is used.

**Result (3 seeds):**
- EV at decision **+5.1% to +6.0% NFL, +3.3% to +4.5% CFB**, 100% of bets +EV.
- Closing value **+5.1% to +5.5% NFL, +1.1% to +2.7% CFB** in every seed: the value is confirmed by the close.
- Volume falls to **49–55 NFL and 113–122 CFB sharp bets** (from 990 / 1,516). Genuine +EV prices at a book that
  re-quotes the retail median are rare; they occur mainly in the ~10-minute window after each new market snapshot.
- Side effect: V4's respected-action re-quote now fires for **0.3%** of bets instead of **25.4%**. In V4, a quarter
  of all customer bets were priced at the market makers' price because −EV pseudo-sharp bets kept triggering it.

**Unresolved:**
- The low volume is limited by collection cadence (median snapshot 17 min old at a sharp decision; prices are only
  known at fetch times).
- Real sharps would also act between our snapshots, which we cannot observe and do not invent.

## 5. High-stakes recreational concentration
- **Who they are:** 736 customers (3.7% of 20,000; 716 bet).
  - 5,352 bets, **56.5% of handle**.
  - Median stake **$1,153** (mean $1,699, max $10,000); median 2.8% of bankroll.
  - 7.5 bets each, about 0.75 per week.
  - They behave like casual customers on side choice (favourites 58% on spreads, overs 56%) and pay the same vig
    (expected ROI −4.2%). Only the size differs.
- **Effect on NEED:** the largest single wager in a game-market is a high-stakes wager in **87–97%** of NFL
  game-markets and 66–88% of CFB, and it is typically **18–28% of that game-market's handle**.
- **Counterfactuals** (existing settlement code, each wager at its own number and price):

  | Change | NEED side reverses (T−1h) |
  |---|---|
  | Remove the largest 1 wager | 29–35% |
  | Remove the largest 5 | 34–43% |
  | Remove the largest 10 | 37–48% |
  | Replace the largest with a median-size wager on the same terms | almost the same |

- **NEED stability decomposition** (matched market data; only the named group re-randomised):

  | Re-randomised | Same T−1h NEED side in all 3 runs |
  |---|---|
  | Everyone | 37% |
  | **High-stakes only** | **39%** |
  | Other recreational only | 91% |
  | Sharps only | 89% |

  **Financial NEED direction is decided almost entirely by which high-stakes customers arrive and how much they
  stake.**
- **Is that realistic?** Concentrated liability is plausible for a real book. But V4's high-stakes count, unit sizes
  and stake noise come from parameters V4 itself labels unvalidated (`v4_params_m3_unvalidated`), and the synthetic
  handle per game-market (~$30k–70k) is small relative to a $1k–10k stake. Without real handle-concentration data
  this cannot be calibrated. **Left unchanged.** Instead:
- **MEASUREMENT added:** `concentration.need_robustness`, i.e. whether NEED survives removing the 5 largest wagers.
  In a single run, robust NEEDs agree across seeds 43% of the time, against 24–27% for non-robust ones. Useful as a
  warning label; it does not make NEED stable.

## 6. Seven-archetype audit (production engine; full numbers in `V4_REALISM_TABLES.md` and `concentration_A0.json`)
| Archetype | Customers (bet) | Bets | Handle share | Median stake | Favourite (spread / ML) | Over | Expected ROI at prices | ROI | Behaves as named? |
|---|---|---|---|---|---|---|---|---|---|
| High-stakes rec | 736 (716) | 5,352 | 56.5% | $1,153 | 58% / 56% | 56% | −4.2% | −6.7% | Yes: a casual customer with a large bankroll |
| Casual rec | 10,349 (9,507) | 86,959 | 17.7% | $20 | 61% / 54% | 57% | −4.3% | −6.3% | Yes |
| Sharp | 614 (565) | 2,506 | 13.3% | $605 | 39% / 22% | 44% | **−1.2%** | −5.0% | **No:** −EV prices (corrected in V4r1) |
| Narrative chaser | 2,392 (2,342) | 26,281 | 6.7% | $24 | 57% / 51% | 59% | −4.3% | −4.9% | Plausible: most active, over-heavy |
| Team loyal | 2,426 (2,278) | 15,915 | 3.4% | $21 | 53% / 50% | 53% | −4.2% | −8.5% | Yes: 31% moneyline (backing their team) |
| Underdog contrarian | 1,597 (1,456) | 8,781 | 2.2% | $24 | **35% / 10%** | 46% | −4.2% | −6.3% | Yes |
| Selective / price-sensitive | 1,886 (317) | 411 | 0.2% | $57 | 59% / 28% | 55% | −3.7% | −1.3% | **Partly:** selective (17% ever bet) but barely price-sensitive |

All recreational archetypes' expected ROI is about −4.2%, i.e. they pay the vig. Realized ROI differs by outcome
variance.

## 7. Hold and risk management
- **Margin.** V4's theoretical margin (about 4.5%, the retail median price) is kept: there is no reason to change
  it. Expected hold at the prices charged is 3.8% (V4) and 4.2% (V4r1); realized 6.3% and 5.6–7.5% across seeds.
- **Exposure.**
  - Per-wager limits almost never bind (0.03% of recreational stakes).
  - Worst-case exposure exceeds a $25k/$15k game limit in only 0.7% / 3.5% of game-markets (earlier lab).
  - A per-game limit would therefore change little, and any specific number would be arbitrary: **not
    implemented.**
- **Respected action.** The re-quote rule is credible once "respected" means genuinely +EV (see §4).

## 8. Exact code changes (development only; branch `research/v4-realism`)
- **`lab_v4/runner.py`:**
  - `sharp_pick_ev`, `sharp_hook`, `_devig`, `_ref_mu` (CUSTOMER BEHAVIOUR CORRECTION);
  - `max_win_cap` (RISK MANAGEMENT IMPROVEMENT);
  - audit recording (`record=True`) and selective re-keying (`rekey`). Neither affects any decision.
- **`lab_v4/config.py`:** configurations `R_SHARP`, `R_MAXWIN`, `R` (= V4r1); A–E unchanged.
- **New modules:** `lab_v4/reconcile.py`, `moneyline.py`, `concentration.py` (including `need_robustness`),
  `ledger.py`, `realism_runs.py`, `realism_report.py`; `season.py` passes the new options through.
- **Tests:** `tests_lab_v4/test_lab.py`, 13 tests. Configuration A remains identical to the production engine.
- **Not modified:** production engine code (`sportsbook_v4/*`), historical wagers, frozen NEED records.

## 9. Before / after (common random numbers; 3 seeds where shown)
| | V4 (A) | V4r1 (R) |
|---|---|---|
| Accounting mismatches | 0 | 0 |
| Expected hold / realized hold | 3.8–3.9% / 5.8–7.4% | 4.2% / 5.6–7.5% |
| CFB moneyline expected (power) / realized / SD | 10.5% / 26.2% / 14.9% | 7.5–8.1% / 15–26% / 8.4–9.1% |
| Largest potential single win | $269,850 | $10,000 |
| Sharp bets (NFL / CFB) | 990 / 1,516 | 49–55 / 113–122 |
| Sharp EV at decision (NFL / CFB) | −2.2% / −1.9% | +5.1–6.0% / +3.3–4.5% |
| Sharp closing value (NFL / CFB) | −2.9% / −3.1% | +5.1–5.5% / +1.1–2.7% |
| Bets priced at market makers (re-quote) | 25.4% | 0.3% |
| High-stakes share of handle | 56.5% | 63.2–65.0% (most sharp handle disappears) |
| T−1h NEED same side across 3 seeds | 37% | 32% |
| Material NEED at T−1h | 377 | 368 |
| NEED side different, V4 vs V4r1 (seed 0) | | 105 of 668 (16%); 39 material |

**Betting performance is deliberately not ranked here.** The corrected sharps' positive ROI follows from their
positive EV at decision and closing value; nothing was tuned to outcomes.

## 10. Remaining unrealistic assumptions
1. **High-stakes customers decide NEED direction.** Their numbers and stake sizes are unvalidated parameters with no
   data to calibrate them (§5).
2. **Moneyline side choice ignores the size of the price;** only the favourite side is capped (§3).
3. **Stale pricing:** recreational bets are priced from a quote with a median age of 66–79 min (collection cadence).
4. **Sharps can act only around our snapshots,** so their volume is cadence-limited.
5. **The selective/price-sensitive archetype is only mildly price-sensitive.**
6. **Recreational customers are price-insensitive** (lab B showed adding sensitivity changes little while V4 prices
   at the market).

## 11. Recommended production changes (each needs your approval)
1. **Adopt the sharp EV rule** (customer behaviour correction): demonstrably mis-specified today.
2. **Adopt the max-win limit** (risk management improvement): standard practice; touches 9 wagers in 20 weeks but
   removes the most extreme variance.
3. **Show "NEED robust to the 5 largest bets: yes/no"** next to Financial NEED (measurement only).
4. **Do not lower the margin. Do not add per-game limits.** Neither is justified by the evidence.
5. **Keep everything else.**

## 12. Deployment and rollback (later, on approval; NOT done)
1. **Port** `sharp_pick_ev` and `max_win_cap` into `sportsbook_v4/engine.py` as engine **sportsbook_v5.r3**:
   - replace the body of `_sharp_pick` (keep the r2 code path selectable for replay);
   - apply the max-win cap after the stake calculation in `_decide`;
   - bump `ENGINE_VERSION` and add the params change, so `params_hash` changes.
2. **Tests:**
   - existing r2 replay tests keep passing via the r2 path;
   - port `tests_lab_v4` checks (sharp EV ≥ threshold, max-win) to `tests_v4`;
   - run the full suite.
3. **Merge to `v4-dev` (your approval).** The live book's existing rule opens a **new weekly run** for the new engine
   version. r2 runs stay in the ledger as superseded and immutable; no historical wager or frozen NEED record is
   rewritten.
4. **Rollback:** revert the merge commit (`git revert -m 1 <sha>`, push; never force-push). The next build opens an
   r2 run again; r3 wagers stay in the ledger as superseded.
