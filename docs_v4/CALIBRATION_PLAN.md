# Realistic public-bettor calibration: report before deployment (2026-10-29)

| | |
|---|---|
| Status | **EXPERIMENTAL, NOT ACTIVE.** Developed on branch `v4-dev-postgame-hold`; not merged, not deployed. |
| Live effect | Live m4 sportsbook output is byte-identical with these changes present (verified: snapshot, page, wagers). |
| Labels | Observed third-party splits and modeled splits are stored, labelled and processed separately. |

## 1. Real betting-split sources

| Source | What it publishes | Markets / sports | Updates | Automated access | Cost |
|---|---|---|---|---|---|
| **VSiN betting splits** | DraftKings (and Circa for NFL) **% of bets and % of handle** per side; two named single books, not market-wide | NFL and CFB: spread, total, moneyline | about every 5 minutes (VSiN's own description) | Not established. Its terms could not be retrieved (blocked in this environment, not found by search). Treat as **manual viewing only** until the terms are read. | free to view |
| **DraftKings Network (DK Network)** | DraftKings "bets %" and "handle %" in daily articles | NFL, CFB, others | daily articles | **Prohibited.** DK Network terms (7/31/24) bar mining or harvesting content "through software or other automated tools"; DraftKings' main terms bar bots, spiders and screen scrapers. Manual reading only. | free to view |
| **Action Network public betting** | % of bets free; **% of money is PRO** (paid); multi-book aggregate of partner books | NFL, CFB | live | Terms not verified (blocked here). Paid tier for money %. | PRO subscription (paid) |
| **Covers consensus** | percentages of **user picks** in Covers contests, **not sportsbook tickets or money** | NFL, CFB | live | not relevant: not betting splits | — |
| **Sportradar Betting Splits API** | ticket and stake % aggregated from 150+ bookmakers (their FAQ: % may be displayed, raw counts may not) | NFL and others | real time | **Licensed** | contact sales; not public; certainly paid |
| **SharpAPI** | splits sourced from DraftKings and Circa (their docs) | NFL and others | API | licensed by their terms | splits in the **Pro plan, $229/month** (Hobby $79 has none) |
| Community scrapers (Apify etc.) | re-scraped Action Network / VegasInsider pages | — | — | **Excluded:** scraping restricted sites | — |
| Published research | VSiN's season summaries of DraftKings splits (CFB 2022–25: 3,173 FBS games; majority handle on spreads 48.9% ATS); academic studies (Paul & Weinbach; Shank 2019; Levitt 2004) | NFL, CFB | seasonal | free to read | free |

**Conclusion:**
- **No free, legal, reliable automated source of real ticket/handle splits was found.**
- The legal options are:
  - (a) **manual entry** of numbers you view on published pages — built (§2);
  - (b) a **paid licensed feed** (SharpAPI Pro $229/month, Sportradar on request), which needs your explicit approval under the no-paid-services rule;
  - (c) **aggregate statistics** from published research, usable as calibration targets but not game-level data.
- Every published split covers **one book** (DraftKings, or Circa) or a **partner aggregate** (Action Network), never the whole market. The store records which.

**Network note:** vsin.com, actionnetwork.com and en.wikipedia.org are blocked in this cloud environment, so their terms pages could not be read directly. They would have to be allowed in the environment's network settings before I could verify them.

## 2. Observation system (built, inactive)

**Store** (`data_v4/observed_splits.py`), one record per selection:
- sport, home, away, kickoff Eastern date, sportsbook;
- aggregation (`single_book` / `multiple_books` / `unknown`);
- market, selection, line;
- ticket %, handle %;
- observed_at;
- source, source_url, collection_method (`manual_entry` or `licensed_feed`; scraping is rejected), entered_by, notes.

**Safeguards:**
- Blank percentages stay blank and are never inferred from the other side.
- Both sides must sum to 100 ± 2.
- Files are immutable and hash-verified, in release `v4-observed-splits`. A re-entered identical observation counts once.
- Label `OBSERVED_THIRD_PARTY`. The synthetic sportsbook never reads this module (tested).

**Manual entry, two ways:**
1. **CSV:** `docs_v4/templates/observed_splits_template.csv` (headers only), imported with `python -m data_v4.observed_splits import file.csv --out DIR`.
2. **Phone-friendly GitHub issue form** ("Observed betting split"):
   - The workflow `observed-splits.yml` validates it, saves an immutable record and replies on the issue.
   - It runs only for issues **you** open, uses no secret, and passes the issue text to the parser as data only.
   - Inactive until merged; each submission would bill about 1 Actions minute.

## 3. Team-popularity model (built; inputs empty)

**Model** (`sportsbook_v4/popularity.py`):
- Indicators: national TV viewers per game, Google Trends search interest, franchise valuation, average attendance and social followers.
- Each indicator is log-transformed and standardised within the sport.
- It is used **only if it covers at least 90% of teams**, because a partial list would quietly make every missing team "average".
- The combined index becomes a multiplier exp(0.25 × z), clipped to 0.6–1.8 and rescaled to a geometric mean of 1, so the total amount of betting is unchanged.

**What popularity affects:** only how often recreational bettors pick a game and their lean within it, through the existing `popularity_weight` traits. **It is not a betting percentage, and no team is assigned a share of wagers.**

**Inputs** (`data_v4/popularity/inputs_<sport>_2026.csv`): every value needs a source, URL and retrieval date. Currently empty: no indicator with full-league coverage could be retrieved here.

**Partial data found, recorded in this report only, not as inputs:**

| Fact | Source |
|---|---|
| 2025 most-watched NFL team: Chiefs | Nielsen via Awful Announcing / Washington Times |
| 2025 Forbes NFL valuations: Cowboys $13B, Rams $10.5B, Giants $10.1B … Bengals $5.25B (last) | Forbes via AOL / Bleacher Report |
| DraftKings 2025 most-bet NFL team: Eagles | DraftKings via Covers |
| Caesars 2025 NFL bet-count leaders: Eagles, Bills, Chiefs | Caesars via Covers |
| CFB 2025 Nielsen averages: Alabama 8.49M, Texas 7.55M, Georgia 7.48M, Ohio State 6.57M, Michigan 5.08M | Nielsen via Sports Illustrated, FootballScoop and the Texas athletics site |

**Proposed full-coverage inputs** (all free and public; each needs a manual export or the domain allowed):
- Google Trends five-year interest for every team, exported manually;
- Forbes NFL valuations, full 32-team list;
- ESPN NFL attendance;
- NCAA official FBS attendance;
- season TV averages from Sports Media Watch or Nielsen releases where every team is listed.

## 4. Current vs proposed recreational behaviour (candidate `m5_draft1`, `sportsbook_v4/params_calib.py`)

| Behaviour | Current m4 | Candidate m5_draft1 |
|---|---|---|
| Favourite vs underdog | fixed pull, the same at every spread (about 62% for casual bettors) | pull depends on spread size: ×0.6 near pick'em, ×1.0 at 3.5, ×1.15 at 7, ×1.25 at 10+. Literature is mixed (big favourites draw more bets, but the preference is non-linear), so this is a starting point to calibrate. |
| Home vs away | mean 0 | unchanged |
| Popular teams | inert (no table) | from the popularity index once inputs exist |
| Primetime / national TV | NFL standalone games only; national TV never set | classification v2 for new weeks (already approved): NFL national = standalone; CFB 7 pm+ ET = primetime |
| Large vs small spreads | no effect | through the curve above |
| Tickets vs handle; high-stakes concentration | high-stakes bettors (4%) have a $40k median bankroll; 60% of NFL spread handle | median bankroll $15k (calibration knob) |
| Timing through the week | day-of-week × hour profiles; game choice favours nearer kickoffs | unchanged (you kept it as is) |
| Stake sizing / bankroll limits | bankroll × unit × noise, weekly budget cap | unchanged except as above; bankroll-driven stakes after settlement are the planned engine m5 step |

Nothing is hard-coded per team or per game. The hooks (`fav_spread_curve`, a variant override for team popularity) are inert unless an experimental variant supplies them.

## 5. Expected effect

`tools_v4/model_effect.py` replayed this week's real games (data through Oct 29, 10:00 UTC) under both models, with the same games, prices and arrivals:

| Statistic | NFL current → candidate | CFB current → candidate |
|---|---|---|
| High-stakes share of spread handle | 60.2% → 36.2% | 43.2% → 22.2% |
| Sharp share of spread handle | 18.8% → 30.1% | 37.3% → 51.1% |
| Top 10% of bettors' share of handle | 84.4% → 75.3% | 83.3% → 77.8% |
| Recreational tickets on favourites | 58.1% → 60.2% | 56.7% → 57.4% |
| …at 7.5–14.5-point spreads | 66.7% → 74.1% | 60.8% → 60.9% |
| Median gap between handle % and ticket % (public side) | 16.3 → 14.1 pts | 19.6 → 14.3 pts |
| Games with ≥ 70% recreational handle on one side | 7 → 7 of 14 | 9 → 7 of 18 |
| Games meeting the C1 five-rule filter (mid-week, point-in-time) | 0 → 1 | 0 → 0 |

**Reading:**
- Smaller high-stakes bankrolls spread recreational money more evenly.
- They also **raise the simulated sharps' share**, whose bankrolls were left unchanged. That is a side effect calibration must address, not something to accept by default.
- Total handle falls (NFL $48.5k → $30.3k) because high-stakes stakes shrink; ticket counts are unchanged.
- Popularity is not yet in effect (no inputs).

## 6. Calibration method and validation requirements (`tools_v4/split_calibration.py`)

**Process:**
1. Match each observation to a game (sport, names, Eastern date).
2. Compute the modeled ticket % and handle % from that model's wagers **accepted at or before the observation time**: all simulated customers, as a book's published split covers all of its customers.
3. Measure:
   - ticket % and handle % MAE, RMSE and bias;
   - directional agreement (same majority side);
   - calibration by observed pressure (majority handle 50–60 / 60–70 / 70–80 / 80+, mean observed vs mean modeled);
   - breakdowns by sport, market, sportsbook and popularity tercile.
4. **Training vs held-out:** split chronologically by kickoff date. Candidate selection and any parameter search use the training period only; the held-out period is reported once. "EVALUATED" requires **≥ 100 held-out observations**; fewer is reported as INSUFFICIENT HOLDOUT. Missing observations are never invented: unmatched observations and games with no model wagers are counted and reported.

**Requirements before any activation:**
- at least about 300 observed spread observations across ≥ 6 weeks, both sports;
- the candidate beats the active model on **held-out** handle % MAE and directional agreement;
- no bias above 5 points in any pressure bin;
- results reported by sport and market.

**Book comparability:** DraftKings is often described as the most recreational US book, so its splits are a reasonable target for the recreational archetypes. Circa splits are the reference for sharp-heavy action. The tool keeps them apart by sportsbook.

## 7. Additional API costs

**None so far.**
- Manual entry and the free public popularity indicators cost nothing.
- **Optional, needs your approval:** a licensed splits feed (SharpAPI Pro $229/month; Sportradar by quote).
- The issue-form workflow would use about 1 GitHub Actions minute per submission.
- No Odds API credits are involved.

## 8. Tests

`tests_v4/test_calibration.py` (24 tests) covers:
- validation, no-fill, pair sums and tamper detection of observations;
- the headers-only template;
- the popularity coverage rule and volume preservation;
- the active parameters' hash unchanged, and the variant restoring popularity;
- favourite-curve defaults;
- metric calculations, the chronological split, the time cutoff and name/date matching;
- end-to-end calibration of both models on a built book;
- issue-form parsing and an owner-only, no-secret workflow.

Full suite on the branch: **511 passed, 22 skipped**. The byte-identity check of live m4 output with the hooks present passed.

## 9. Approvals needed before activation

1. **Collection method:** manual entry (free); or a paid licensed feed (state which and the monthly cap).
2. **Merge the observation system** (store, CSV import, issue form and workflow) into `v4-dev`. This is collection only; it changes no model.
3. **Popularity inputs:** approve the free sources in §3, and either allow the needed domains in this environment's network settings or provide the exports.
4. **After calibration results meet §6:** approve a specific calibrated candidate. It would become a new parameter version that starts with the **next betting week** (Tuesday 12:00 UTC), never this week. Existing wagers, Need decisions, cohort memberships and results are unaffected.

Until then nothing here runs live, and every strategy stays WATCH / paper.
