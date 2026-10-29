# Book Need Tracker: exploratory historical sensitivity test (2026-10-29)

> **EXPLORATORY SYNTHETIC BACKTEST.** This is not evidence that fading real public bettors is, or is not, profitable. The bettors are our simulated population with unvalidated assumptions. It only shows how often the side **our model's** public overbets would have covered.

## Method (`tools_v4/need_history.py`)

**Population:** engine m4 with the live parameters and 10,000 simulated NFL bettors.

**Games:** NFL regular seasons 2016–2025, 2,634 games, from nflverse `games.csv` (downloaded 2026-10-29).

**Prices:**
- Each game shows customers one price all week: nflverse's closing spread and its listed spread odds, or −110 when missing.
- There is no line movement, so the simulated sharps rarely find an edge.

**Measurement:**
- At kickoff: the book's Need and the pre-registered filter, the same code as the live tracker.
- The Need side is graded against the real final score at that closing price.

**Run:** reproduce with `python tools_v4/need_history.py --games games.csv --seasons 2016-2025 --out out.json`. Summary: `docs_v4/need_history_2016_2025.json`. Runtime about 22 minutes.

## Results

| Set | Games | ATS W-L-P | Win % (95% CI) | ROI at price (95% CI) |
|---|---|---|---|---|
| All Need sides | 2,634 | 1292-1278-64 | 50.3% (48.3–52.2) | −2.9% (−6.5% to +0.8%) |
| Research subset (qualified and aligned) | 24 | 12-11-1 | 52.2% (33.0–70.8) | −0.2% (−38% to +38%) |
| Qualified but disagreeing | 0 | — | — | — |

Break-even at the recorded prices is about 52.4%.

**By season (all Need sides):** win rates from 45.4% (2023) to 59.7% (2020). Only 2020 has an interval above 50%: the season played with few or no fans, and one of ten seasons. That is the pattern expected from chance across ten tries, and it wasn't pre-registered.

**By strength of recreational pressure** (aligned games, recreational handle share on the public side):

| Rec handle on public side | ATS |
|---|---|
| 50–60% | 631-632 (50.0%) |
| 60–70% | 514-495 (50.9%) |
| 70–80% | 104-99 (51.2%) |
| 80%+ | 5-8 (38.5%) |

**What the Need usually is:**
- In this model the Need side is the **underdog 86% of the time**, because the simulated public leans to favourites.
- Need on the underdog: 1119-1082 (50.8%). Need on the favourite: 173-196 (46.9%).

**Why the filter almost never qualifies:**

| Rule failed | Games |
|---|---|
| recreational handle ≥ 70% on one side | 2,412 |
| top 25% of weekly handle | 1,934 |
| recreational ticket majority on that side | 380 |
| largest bettor > 25% | 3 |
| fewer than 100 wagers | 0 |

With a full week of simulated betting, the median game has about 60% of recreational money on one side. 70% is rare.

## Reading

1. **No sign of an edge in this synthetic test.**
   - Betting every Need side lands at a coin flip, below break-even after the commission.
   - Stronger pressure shows no consistent improvement.
2. **The pre-registered filter is very selective in this model:** about 2–3 qualified games per season. Even a real edge would take many seasons to confirm at that rate. The live tracker sees the same pattern this week: most games fail the 70% rule.
3. **Why it can't settle the question:**
   - Real public money is concentrated differently: popular teams, primetime and parlays are not modelled.
   - Real books move lines in response to that money.
   - The live tracker records both of those effects prospectively, so it, not this test, is the evidence that counts.
4. **No thresholds were changed after seeing these results.** Changing them now would be fitting to noise. Any change needs a new pre-registration and starts a fresh prospective count.
