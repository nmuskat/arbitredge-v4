# ArbitrEdge NEED engine v6 (`need_v6`): DEVELOPMENT, not deployed

Status: built and tested on branch `dev/need-engine-v6`. **Nothing here is live.** Production is unchanged until the
owner approves the steps in §7. Everything is MODELED: a synthetic sportsbook with simulated customers. No NEED is
a real sportsbook's liability, and no NEED is a bet recommendation (rule 6: default NO BET).

## 1. What it is

A synthetic sportsbook that **sets its own prices** with a modeled trading desk, run chronologically on the real
collected odds. It produces four separate fields per game-market (spread, total):

| Field | Meaning | When empty |
|---|---|---|
| FINANCIAL NEED | The outcome the book's accepted wagers most need. Computed by the existing `sportsbook_v4.settlement` code, each wager at its own number, price and stake. Plus materiality, worst case, and expected book P&L at the market makers' fair. | BALANCED / no wagers |
| TRADING DESK NEED | The side the desk *deliberately* chose to be exposed to (ACCEPT EXPOSURE), labelled "risk acceptance (no validated informational edge)" | NONE |
| MARKET-MAKER INFERENCE | Observable pricing only: Pinnacle vs the recreational books, normalised. `candidate_need_side` is **always UNKNOWN**, because odds cannot reveal a professional book's liability. | UNKNOWN |
| ARBITREDGE NEED (headline) | Policy `desk_material_first_v1`: (1) a material desk position → source TRADING DESK; (2) else a material financial NEED → source FINANCIAL; (3) else NONE. The source is always printed. | NONE |

## 2. Components (files)

| File | Role |
|---|---|
| `need_v6/config.py` | Every assumption in one place, with `CONFIG_HASH` (currently `7575496ffcb7`). None was fitted to game results. |
| `need_v6/normalize.py` | Push-aware key-number normalisation. NFL uses the sims_v4 distribution; CFB uses a plain normal (no key weights, to avoid look-ahead). |
| `need_v6/market.py` | Point-in-time market state. Only quotes fetched at or before *t* are used. Fair = Pinnacle no-vig mean, else the BetOnline-group median. STALE after 6 h. Recreational books alone never set fair. |
| `need_v6/book.py` | Wager-level exposure over every final margin/total (pushes, alternate numbers, middles exact) |
| `need_v6/desk.py` | Trading desk with five actions: MOVE / DEFEND (NFL key numbers 3, 7) / BALANCE (game limit; closes a side past 1.25×) / ACCEPT EXPOSURE (settled price, material, within limit, EV ≥ 0.20·SD) / NO POSITION. Every decision is logged with its rationale. The desk never opposes money just because it is popular. |
| `need_v6/customers.py` | The existing population. Game, market and side preferences are reused from the live engine. **New:** recreational price sensitivity (a worse price means fewer bets); sharps bet only when +EV against the fair at the same observation the desk has (cadence-independent); per-wager limits by sport/market with a kickoff ramp; lower limits for respected customers; closed sides refused. |
| `need_v6/engine.py` | Event-driven loop. Observations come before arrivals at the same second. No look-ahead. Deterministic wager ids. Freeze = last decision at or before kickoff − 30 min. |
| `need_v6/need.py` | Builds the card (the four fields above plus book state) |
| `need_v6/week.py` | One sport-week: live game selection, live population ids, chained bankrolls |
| `need_v6/freeze.py` | Write-once frozen records `v6need_<sport>_<event>_<market>.json.gz` in `v4-need-YYYY-Www`. Graded at the exact frozen number and price. Late-written records are kept but excluded from validation. |
| `need_v6/results_match.py` | FCS-aware CFB score matching. Result: 685 of 688 2026 CFB games matched; Cal Poly @ San José State and Maine @ App State remain unmatched and are never guessed. |
| `need_v6/replay.py` | Season replay and grading (DEVELOPMENT data) |
| `need_v6/site.py` | Site block, run only when `NEED_V6=on` |
| `web_v4/live_site.py` | Flag-guarded build step, plus the v6 panel at the top of the "ArbitrEdge Need" tab. Everything else is unchanged. |
| `.github/scripts/site_inputs.sh` | One added line that downloads saved `v6need_*` records, so write-once holds across builds. Harmless while the flag is off. |
| `tests_need_v6/` | 51 tests |

## 3. Checks that passed (development)

- **Accounting.** Every replayed wager settled (0 unsettled across 20 sport-weeks, 144,833 wagers). Each week, customer bankroll change = customer P&L = −book P&L. The desk's exposure grid equals the settlement module's P&L at every tested final score.
- **No look-ahead.** A run stopped at time *t* reproduces exactly the full run's history before *t*. A wild quote added after *t* changes nothing before *t*.
- **Determinism.** The same inputs give identical wagers, decisions and cards. All wager ids are unique.
- **Flag off.** The NEED tab's visible text is identical to the current production code for NFL and CFB (browser check). No page errors either way.
- **Freeze collision fixed.** v6 files never match the Need tracker's `need_*.json.gz` pattern. Without this fix, the existing tracker and the seal step would have crashed.

## 4. Season replay, 2026 weeks to 10 Nov (DEVELOPMENT: games already studied, NOT validation)

| NEED type | Selections | Record | ROI at frozen price | Mean CLV |
|---|---|---|---|---|
| ARBITREDGE NEED (official) | 326 of 668 (342 NONE) | 149-168-9 | −10.4% | −4.1% |
| Financial NEED | 647 | 296-339-12 | −11.0% | −4.2% |
| Trading Desk NEED | 67 | 29-37-1 | −16.9% | −4.3% |

- Official NEED by market: spreads 100-101-7 (−5.1%), totals 49-67-2 (−19.5%). NFL 76-85-5; CFB 73-83-4.
- Source mix: FINANCIAL 259, TRADING DESK 67, NONE 342.
- Desk actions: NO POSITION 14,684 · MOVE 1,684 · BALANCE 1,025 · ACCEPT EXPOSURE 794 · DEFEND 66.
- Synthetic book: $11.87 M handle, +$362 k book P&L (MODELED).
- **Reading.** The NEED is the book's position, not betting value. The frozen price includes the desk's margin and the side customers paid up for, so CLV ≈ −vig, as expected. Nothing here suggests betting a NEED. When a Trading Desk NEED exists, it was always the same side as the Financial NEED (67 of 67): the desk accepts positions customers created; it has no independent opinion.

## 5. Real historical NEED cards (frozen, from the replay)

Full cards: `docs_v4/NEED_V6_CARDS.md`. Summary:
- 49ers @ Rams (NFL spread, 11 Sep): **49ers +3.5 at −105, TRADING DESK**. ACCEPT EXPOSURE, worst case $5,470 of $25,000, EV $1,823 ≥ 0.2 × SD $7,456. Graded a win; CLV −4.2%.
- Giants @ Rams (22 Sep): **Giants +7 at −122, TRADING DESK**. Graded a loss.
- Broncos @ Chiefs (15 Sep): **Broncos +2 at −107, FINANCIAL**. The desk was in BALANCE with Chiefs closed (127% of the limit). Graded a loss.
- Clemson @ LSU (CFB, 5 Sep): **Clemson +9.5 at −102, FINANCIAL**. Graded a loss.
- Dolphins @ Raiders total (13 Sep): **Under 40.5 at −112, FINANCIAL**. Graded a win.
- Jets @ Titans (13 Sep): **NONE**. Financial NEED was Titans −1 but MINOR (swing $3,108).

## 6. Limitations (honest)

1. Desk fair = the market makers' price. The desk has no independent edge, and its NEED is never presented as one.
2. Trading Desk NEED has so far always agreed with Financial NEED.
3. CFB uses no key numbers (plain normal), and CFB coverage is AP Top-25 games only.
4. Moneyline is out of v6 scope (spreads and totals only).
5. Site mode starts each week from **opening** bankrolls. Saving a v6 bankroll chain between builds is not built yet; the replay does chain bankrolls.
6. The build re-simulates the current week on each run. That adds about 20 s per build (measured 19.2 s locally on the 8 Nov week), roughly a third of a 2-minute billed build slot.
7. On-page validation covers frozen records from this and last ISO week only, because site_inputs downloads two weeks. The full cumulative record needs a separate grading run.
8. Validation counts only records written within 3 h of kickoff. Records written later (for example, games already finished when the flag is first switched on) are kept but not counted.
9. Leadership/timing between books is limited by the collection cadence (median 101 min between fetches). Moves between fetches are unknown and never invented.
10. All 2026 results above are development data. Prospective validation begins only from deployment, and the research protocol is **not** frozen (that needs the owner's approval).

## 7. Production deployment (owner approval required; NOT done)

1. **Approve the merge.** Merge `dev/need-engine-v6` into `v4-dev`. With the flag unset, nothing changes on the site or in the data. The only live-path change is the extra download line in `site_inputs.sh`, which finds no `v6need_*` files.
2. **Approve the workflow edit.** In `.github/workflows/site-build.yml`, step "Build site", add:
   ```yaml
        env:
          NEED_V6: ${{ vars.NEED_V6 }}
   ```
   This passes no secret.
3. **Owner switches it on.** GitHub → Settings → Secrets and variables → Actions → Variables → New variable `NEED_V6` = `on`. The next site build shows the v6 panel and starts freezing records into `v4-need-YYYY-Www`.

## 8. Rollback

- **Instant:** delete the `NEED_V6` variable (or set it to anything other than `on`). The next build shows the page exactly as before. Frozen v6 records already saved stay in their releases; they are immutable by rule, so do not delete them.
- **Full:** revert the merge commit on `v4-dev` (`git revert -m 1 <merge sha>`, then push; never force-push).
