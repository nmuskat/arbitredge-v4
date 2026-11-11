# Thursday Night Football pilot: Washington Commanders @ New York Giants (NFL week 10)

**Frozen 2026-11-11 16:02:54 UTC**, about 33 h before kickoff (2026-11-13 01:15 UTC). The machine-readable record is
`pilots/TNF_2026_W10_WAS_NYG.json`. Its checksum is the field `sha256_of_record_without_this_field` =
`9efbc48eed6ae5260a1c55e502f99545ff42389b0111e2a820b5a3fff48ffddc`, and the git commit time proves pre-kickoff
freezing. **This file must not be revised after kickoff.** A pre-kickoff update, if any, goes in a separate,
timestamped revision file.

This is a read-only research pilot. Nothing is deployed, merged or changed in production or in the SIMS project. No
API credits were used. One game cannot validate anything.

## Verified state
- **Production:** `v4-dev` at **8c003b8**, engine **sportsbook_v5.r2**. The published site was built at 2026-11-11
  13:14 UTC from that commit.
- **Live book:** NFL week run `14605149fa17…`, data complete through **2026-11-11 13:13:10 UTC**.
- **SIMS:** repository `nmuskat/arbitredge-sims` at **2c9086c**, model **1.7.2** (fingerprint `350c0c2b6858d738`),
  10,000 simulations, seed 239480049622238, data cutoff 2026-11-09 (nflverse play-by-play through week 9).
- **Reference market** (latest collected snapshot, 2026-11-11 13:13 UTC):
  - spread **NYG −2.5**, Pinnacle −107/−105, Pinnacle no-vig fair = NYG by 1.8;
  - total **41**, Pinnacle −108/−108, fair 41.5;
  - opened NYG −1.5 and 43.5 on 9 Nov.

## 1. Financial NEED (synthetic book; real liability unknown)
| | Spread | Total |
|---|---|---|
| NEED side | **Washington** | **Over** |
| Book P&L if NYG covers / Over | −$16,513 | +$8,237 |
| Book P&L if WAS covers / Under | +$18,360 | −$6,842 |
| Handle, tickets | $27,901, 301 | $16,507, 171 |
| High-stakes recreational | 14 bets, $17,458 (13 on NYG) | 5 bets, $7,699 (all Under) |
| Sharp | 1 bet, $703, WAS +2.5 | 2 bets, $1,769, Under |
| Survives removing the 5 largest wagers | **Yes** (−$6,587 / +$7,806) | **No** (flips to Under) |
| Survives removing all high-stakes wagers | Yes | Yes (barely: +$538 / +$316) |
| Same side in 21 independent simulations of this week | **16 / 21** (WAS) | **17 / 21** (Over) |

**How stability was measured.** The production run was reproduced exactly: seed 0 matches production bet for bet,
with the same run id, random key, quotes and bankrolls. It was then re-run with 20 independent random seeds.

**Production finding (minor; no action taken).** The 10 Nov run's week-open bankrolls were built from the settlement
files available when it was created. They do not include the settlement file written 7 minutes earlier
(`settle_20261110T160049Z`, the Monday-night game).

**NEED keeps changing until kickoff.** The production Need tracker freezes its own pre-kickoff snapshot; grade both.

## 2. Independent trading desk (did not see NEED or SIMS)
- **Spread: NEW YORK LEAN** (weak; no validated predictive advantage).
- **Total: NO POSITION.**

**Verified** (2026 nflverse play-by-play weeks 1–9, practice and roster data):
- **Efficiency (EPA per play):** NYG offence 15th, defence 11th (defensive success rate 7th); WAS offence 20th,
  defence 20th (defensive success rate 26th).
- **Weeks 6–9:** NYG defence −0.131 EPA/play allowed, offence +0.038; WAS offence −0.053, defence −0.002.
- **Line proxies:** NYG allows a sack on 8.9% of dropbacks (27th), WAS 6.4%. WAS has the 2nd-fewest giveaways and is
  5th in takeaways (unstable stats).
- **Pace:** WAS 61.5 plays/game, NYG 64.9 (league 61.5).
- **Quarterbacks:**
  - WAS: Jayden Daniels, EPA/dropback −0.017 on 195 dropbacks.
  - NYG: J.J. McCarthy +0.139 on 74 dropbacks (small sample); NYG also used Winston (−0.085) and Dart.
- **Week-10 practice:** both QBs full. WAS C Lucas Patrick and TE Yankoff did not practise; Burks limited. NYG
  Skattebo, Nabers, C. Jones, T. Johnson and A. Thomas full; Carter, Harrison and Hood limited.
- **Roster status:** WAS without McLaurin, Tunsil and Payne; NYG without Burns, Edmunds and Dart.
- **Context:** both teams on 4 days' rest; division game; the week-5 meeting ended 27–27.
- **Market move:** toward NYG on the spread and down on the total.

**Uncertain or unavailable:**
- **McCarthy's hip:** reported, per search results, that the Giants feel "fairly good"/"confident" he plays. The
  source pages could not be opened here, so this is not verified.
- **Game designations:** not yet published.
- **Weather:** unavailable (sources blocked); not estimated.
- **Line play:** no pressure or coverage charting; line matchups only by proxy.

**Reasoning.**
- *Spread:* efficiency and recent form favour NYG by more than the ~1.8-point market fair, and WAS is missing its top
  receiver and left tackle. That is tempered by small samples, the NYG QB situation, NYG's pass protection, and a
  market that has already moved toward NYG.
- *Total:* the evidence is mixed: a strong recent NYG defence and a weak WAS offence, against a 27–27 first meeting and
  an already lowered total.

## 3. ArbitrEdge SIMS (established procedure: model 1.7.2, 10,000 simulations)
- **Predicted winner:** **Washington**. Win probability WAS 50.4%, NYG 48.8%, tie 0.8%.
- **Mean score:** NYG 21.24, WAS 21.62. Median 21–21. Representative game WAS 23–20.
- **Home margin:** mean −0.4, median −1; 10th–90th percentile −16 to +15.
- **Total:** mean 42.9, median 43; 10th–90th percentile 26 to 60.
- **Fair lines:** NYG +0.5; total 42.5.
- **At the reference lines:**
  - NYG −2.5 covers **44.5%**, WAS +2.5 **55.5%**;
  - total 41: **Over 52.5%, Under 44.0%, push 3.5%** (41.5: 52.5% / 47.5%).
- **Key uncertainty:**
  - Designations unpublished, so by SIMS's own rule recently absent players play only at observed return rates
    (NYG Skattebo 39%, T. Johnson 23%, C. Jones 30%, N. Harris 30%; WAS Burks 39%), even though several practised
    fully. This likely understates NYG.
  - McCarthy is assumed to start.
  - Weather is not modelled.
  - **SIMS NFL accuracy has been worse than the market in every held-out set** (`docs/ACCURACY_REPORT.md` in the
    SIMS repository).
- **Provenance:** this is a local research rerun in a fresh database built from free nflverse data. It is not an
  official SIMS-ledger forecast.

## 4. Comparison
| Market | Financial NEED | Trading desk | SIMS |
|---|---|---|---|
| Spread (NYG −2.5) | **Washington** (robust; 16/21 runs) | **NEW YORK LEAN** | **Washington** (55.5% to cover +2.5) |
| Total (41) | **Over** (fragile; 17/21 runs; flips without the 5 largest bets) | **NO POSITION** | **Over** (52.5% vs 44.0%) |

- **All three agree:** nowhere.
- **NEED and SIMS agree** on Washington and the Over. That agreement is not independent evidence:
  - NEED's spread side is mechanical (synthetic customers back the home favourite, so the book needs the underdog);
  - the total NEED is set by five high-stakes Under bets.
- **Desk and SIMS** use partly shared inputs (2026 play-by-play) yet disagree on the spread. SIMS's availability rule
  removes NYG skill players the desk expects to play.
- **Research selection: PASS on the spread, PASS on the total.** No component has validated predictive value, the two
  model-based views conflict on the spread, and the only total views are a fragile NEED and a small SIMS lean.

## 5. Post-game evaluation (fixed now, before the result)
Each component is graded at the **reference lines (NYG −2.5, total 41) and Pinnacle's prices above**:
- **NEED:** WAS +2.5 −105; Over 41 −108.
- **Desk:** NYG −2.5 −107.
- **SIMS:**
  - Brier score of its win probability, and log loss of its cover / over probabilities (pushes excluded);
  - absolute error of its mean margin and mean total;
  - comparison with Pinnacle's fair (NYG by 1.8, total 41.5).
- **Also recorded:**
  - the production Need tracker's own final pre-kickoff NEED (it may differ from this snapshot);
  - closing-line movement from the reference snapshot to the last pre-kickoff snapshot.
