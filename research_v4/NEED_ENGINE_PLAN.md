# ArbitrEdge NEED Engine: Minimal Practical Plan

*Plan only. No production change, deploy, collection change or spend. 2026-11-11.*

Goal: an operational **ARBITREDGE NEED** per NFL/CFB game, built on the existing synthetic bettors, ledger,
settlement and Financial NEED. No rebuild, no new paid data.

## 1. What already works (verified today)

| Component | Status | Evidence |
|---|---|---|
| Synthetic bettors (7 archetypes, 10,000 per sport) | **Working** | Varied, bias-driven sides (favourites/home/overs/popularity/primetime/loyalty/recency), lognormal stakes, persistent bankrolls |
| Wager ledger, replay, settlement | **Working** | Immutable ledger; replay reproduces live NFL week 8 exactly (12,001 wagers) |
| **Financial NEED** (`sportsbook_v4/settlement.py`) | **Correct** | Uses every wager's **own line, price and stake**. At actual finals, its P&L function matches the settlement ledger on **all 620 spread/total game-markets** within $0.18 (cent rounding). |
| Frozen pre-kickoff NEED snapshots + grading | **Working** | `need_tracker.py`; existing v4-need releases |
| Market prices (10 books incl. Pinnacle) + no-vig consensus | **Working** | `market_v4/asof.py` |

## 2. What is broken or missing (largest first)

1. **Recreational bettors ignore price.** They choose sides from preferences only. If the book moves the line or juice, their betting doesn't change. *A trading desk can't influence customers who don't respond to price.*
2. **The book doesn't set its own prices.** It copies the market consensus with a 10-minute lag (and copies market makers after sharp bets). There's no desk decision.
3. **No betting limits** except a $3,000 sharp cap. Real books cap per customer and per game, with limits rising toward kickoff.
4. **The sharp model depends on how often we collect odds.** Sharps lost in the sparse replay weeks and won in the denser live weeks.
5. **Financial NEED has no materiality threshold.** A $50 difference produces a "NEED" just like a $50,000 one. It reports liability only, not expected P&L under the fair price.
6. **73 CFB games lack final scores** (FCS opponents and name mismatches in result matching), so their wagers stay unsettled.

## 3. Main differences from a professional sportsbook (Circa-style)

| Professional book | ArbitrEdge today |
|---|---|
| Sets and shades its own price; moves on respected action | Copies the market |
| Limits by customer and game, rising toward kickoff | Almost none |
| Respected customers move the line | Partial (copy-after-sharp) |
| Customers respond to price | Only sharps and one small group |
| Decides to balance **or** deliberately hold exposure (manager: "book hard to the side we want to need") | No decision layer; Financial NEED is accidental exposure |
| Hold about 3-5%, sides > totals | Built-in margin 4.4% (fine); sides ≈ totals |

## 4. Minimal trading-desk layer: Desk v1

Runs at every existing site build (no new schedule), per game-market, using only data available at that moment.

**Inputs (all existing):**
* current exposure (Financial NEED, swing, worst case, handle);
* recreational and sharp handle since the last build;
* market fair price = Pinnacle/consensus no-vig;
* uncertainty = disagreement between books' fair lines (minimum 0.25 pt);
* the book's posted line.

**Risk limit (assumption, configurable):** maximum worst-case loss per game = **$25,000 NFL / $15,000 CFB**. This is roughly the "3-5 limit bets" scale at our simulated stake sizes.

**Decision order (first rule that fires wins):**

| # | Rule | Action | Trading Desk NEED |
|---|---|---|---|
| 1 | **Respected action:** sharp handle ≥ $3,000 on side S since the last build | **MOVE** toward S (price discovery) | **NONE** (price not settled) |
| 2 | **Risk:** worst-case loss > limit | **BALANCE** (shade 10-20 cents against the heavy side) | **NONE** |
| 3 | **Price off fair:** \|posted − fair\| > uncertainty | **MOVE** to fair | **NONE** |
| 4 | **Deliberate position:** price settled (no respected action for 2 builds, posted within uncertainty of fair) **and** recreational money ≥ 60% on side S **and** worst case within limit | **ACCEPT EXPOSURE**: hold the price (optionally shade 5 cents against S) and let liability build | **Opposite of S**, reason recorded |
| 5 | Otherwise | **NO POSITION** | **NONE** |

**Why rule 4 is a deliberate choice rather than just balancing:** at a price the desk trusts, lopsided recreational demand is profitable to hold. That is the documented Levitt (2004) behaviour and the Circa manager's "book hard to the side we want to need". The desk's reason is **customer flow at a settled price within its risk limit**, not Pinnacle's opinion. Rules 1-3 show when it declines to take a position.

*Honest note:* in rule 4, Desk NEED will often match Financial NEED, because both oppose the recreational money. They differ when the desk declines (respected action, risk limit or unsettled price gives Desk NEED = NONE while a Financial NEED exists), and in **magnitude/intent**. Neither predicts game outcomes by itself.

## 5. ARBITREDGE NEED output (per game-market)

```
GAME: <away> @ <home> · <kickoff> · market: spread | total
FINANCIAL NEED      side + liability: P&L if each side covers, swing $, worst case $, middle risk, handle
                    status: MATERIAL (swing ≥ max($5,000, 5% of game handle)) | MINOR | BALANCED
DESK ACTION         MOVE | BALANCE | ACCEPT EXPOSURE | NO POSITION  (rule # that fired)
TRADING DESK NEED   side or NONE · reason (one sentence) · risk used: worst case $ vs limit $
MARKET              posted line · market fair (no-vig) · uncertainty · Pinnacle vs rec books
LABELS              MODELED synthetic sportsbook · NOT real sportsbook liability · NOT a bet recommendation
VALIDATION          Desk-NEED record to date (W-L, CLV) — shown, never implied as proof
```

## 6. What can use existing infrastructure now (no new data, no spend)

| Step | Work | Effort | Changes production? |
|---|---|---|---|
| **A** | Materiality status for Financial NEED; expected book P&L under the fair price (key-number normalisation already written) | 1 day | No: research branch first |
| **B** | Desk v1 as a **read-only layer** (rules 1-5 computed on the existing book; no feedback into bettors) + NEED card JSON | 1-2 days | No: research branch first |
| **C** | **Replay the 2026 season** through Desk v1: every decision uses only pre-kickoff data; desk actions and NEED cards per game; Financial vs Desk NEED vs Pinnacle side, results and CLV reported (development, not proof) | 1 day | No |
| **D** | Fix FCS result matching (73 CFB games) | 0.5 day | Research first; production with approval |
| **E** | *(Approval)* Show the NEED card on the site next to the existing Need tab; freeze it pre-kickoff like current snapshots | 1 day | **Yes: needs "Deploy"** |
| **F** | *(Approval, phase 2)* New engine version where the desk's price **feeds back**: modest price sensitivity for recreational bettors, per-game/per-customer limits. Runs alongside v5; v5 untouched. | 1-2 weeks | Research, then approval |

**Immediately testable after A-C:**
* Does the desk take positions on a sensible share of games (not every game)?
* Do risk limits bind?
* How often does Desk NEED differ from Financial NEED?
* Records and CLV for each NEED type. These are tracked, never claimed as edge.

## 7. Approval checkpoints
1. Approve **A-D** (research branch, no production effect).
2. Review the replay results and NEED cards. Then approve **E** (site display), or not.
3. Approve **F** separately later.

**Not claimed:**
* that simulated NEED is real sportsbook liability;
* that Desk NEED predicts outcomes;
* that any NEED is a profitable bet.

Profitability stays a separate, measured question.
