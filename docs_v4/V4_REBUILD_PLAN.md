# ArbitrEdge V4 — Rebuild Plan (First Assignment, A–H)

Date: 2026-10-26 · Status: for owner review · Production: untouched (no access from this environment)

---

## 0. Where things stand, plainly

**Built and tested (in this cloud sandbox; nothing deployed):**

| Component | State | Tests |
|---|---|---|
| Engine A core — event-sourced synthetic sportsbook (`sportsbook_v4/`) | Milestones 1–2 | 64 passed |
| Decision core — `edge_v4/`: vig removal, EV with pushes, BET/WATCH/NO BET gate, fractional Kelly with caps, Odds API credit gate, validation statistics | **New today** | 41 passed |
| Odds API outage patch (V3, not deployed) | Done | 33 passed |

- Engine A already has persistent bettors (default 10,000 per sport), an immutable accepted-wager ledger, exact P&L-by-margin exposure, and per-wager settlement including pushes and middles. Totals are not yet in Engine A.
- I injected six deliberate regressions into `edge_v4/` to check that the tests catch them, and all six were caught. That exercise exposed a real bug, now fixed: a bootstrap p-value could be exactly 0, which makes a multiple-testing correction do nothing.

**Withdrawn:** the earlier setup package installed V4 tooling on the production server. With about 1 GB RAM and a disk 98% full, that server must not host V4. **Do not run `arbitredge_v4_dev_setup.sh` on production.** It would refuse anyway, because of its 3,700 MB RAM minimum. Its isolation and secret-scanning logic will be reused for the new development server.

**Production risk to flag (no action taken):** a 24 GB disk at 98% leaves about 0.5 GB free next to an 8.6 GB SQLite database that keeps growing. SQLite does not corrupt on a full disk, but writes fail, so the V3 collector would stop saving snapshots. This needs your decision independently of V4.

**Limits of this sandbox:**
- It is ephemeral and has no GitHub connection, so work survives only as the bundles I send you.
- It cannot reach `the-odds-api.com`; the network policy blocks it.
- It cannot reach your server.

It is suitable for writing and testing code against fixtures, but not for collecting data, hosting the website, or running backtests on the real database. Section E is the fix.

---

## A. Proposed V4 system architecture

```
                    ┌──────────────── INGESTION (credit-gated, provenance on every row) ─────────────────┐
 The Odds API ──►   │ odds collector V4 ─► raw_snapshots (append-only: source, book, fetched_at,        │
 nflverse / CFBD ─► │ stats/pbp loader     request fingerprint, credits charged)                         │
 NWS weather ─────► │ weather, injuries  ─► data-quality monitors: stale · missing · duplicate game ·    │
 injury reports ──► │                       team-match failure · suspicious move · provider outage      │
                    └────────────────────────────────────┬────────────────────────────────────────────────┘
                                                         ▼
                        POINT-IN-TIME STORE ("as of T" views; nothing after T is visible)
              ┌──────────────────────┬──────────────────┴────────┬────────────────────────┐
              ▼                      ▼                           ▼                        ▼
      ENGINE A                ENGINE B                    ENGINE C                 MARKET BASELINE
      Synthetic book          Sharp-money evidence        Football model (SIMS)    no-vig consensus,
      10k+10k bettors,        price-based signals,        score distributions,     sharp-book weighted
      wager ledger, Need      0–100 score (experimental   fair lines, calibrated   (the benchmark every
      (liability only)        until calibrated)           probabilities            other signal must beat)
              └──────────────────────┴──────────────┬────────────┴────────────────────────┘
                                                    ▼
             DECISION LAYER D (edge_v4) — signals enter only with out-of-sample incremental value
             EV → uncertainty-adjusted edge → BET / WATCH / NO BET (NO BET is the default)
                                                    ▼
             RISK: fractional Kelly on the pessimistic probability · game/day/correlation caps · drawdown throttle
                                                    ▼
             PAPER LEDGER (immutable) ─► VALIDATION (walk-forward, CLV, ROI CIs, multiple testing) ─► PERFORMANCE CENTER
                                                    ▼
             WEBSITE — every number carries a label: OBSERVED · SYNTHETIC · MODEL · EXPERIMENTAL · VALIDATED
```

**Design decisions:**
1. **The market is the null hypothesis.** The no-vig consensus price is the baseline model. A signal earns weight only if it improves out-of-sample prediction beyond that baseline. I test this with likelihood-ratio tests on a logistic model of outcome given the market probability plus the signal, Holm-corrected. Engines A, B and C are *not* weighted equally, and none is assumed to work.
2. **Point-in-time everywhere.** Every input has an `observed_at` timestamp. Backtests read through as-of views. `assert_point_in_time` makes a run fail if any input postdates its decision (already implemented and tested).
3. **Append-only ledgers** for wagers, decisions, paper bets and credit usage, enforced by database triggers (pattern proven in M1).
4. **Storage:** PostgreSQL for operational data (collector, website and simulations write at the same time), plus Parquet/DuckDB for historical research. Both are self-hosted on the dev server at no extra cost.
5. **Versioning:** every model, parameter set and policy has a version string and a hash stored with each output, so any number on the site can be reproduced.
6. **The website** is a separate process that reads only from the database: FastAPI with server-rendered pages and small charting. It is fast on mobile and keeps the blue/orange brand. It cannot write data.

---

## B. Reuse vs rebuild

| Source | Reuse | Rebuild / discard |
|---|---|---|
| **V3 production data** | **Collected odds snapshots and results.** These are the most valuable asset, because historical prices cannot be re-collected without spending credits. Also team identity mapping (`identity/`), result backfills, coverage audits. | — |
| **V3 code** | Collector request/parsing knowledge. The outage patch (provider error preservation, circuit breaker, freshness rules). `market_state_as_of` / `context_state_as_of` ideas (point-in-time views). Brand palette and assets. | `bet_selection.py` (home bias, Defect A); stake shift (B); no persisted wagers (C); `_promote_v3_live` overwriting the Need (D); mutable `experimental_mixed_signals` (E); the official-play process. |
| **V4 prototypes** | `sportsbook_v4/` as Engine A's core: ledger, settlement, grader, replay, determinism. `edge_v4/` as Layer D. | Behaviour parameters are unvalidated placeholders, and labelled so. Totals still need adding. Team popularity stays neutral until a sourced dataset exists. |
| **ArbitrEdge SIMS** | **Not evaluated: I have never received the SIMS code.** Protocol once supplied: reproduce its historical predictions *with the timestamps they were made*. Score log loss, Brier and calibration against the market baseline, out of sample only. Integrate it only if it adds information beyond the market. | If its historical outputs cannot be shown to be point-in-time, they cannot be used as evidence of performance, only re-run forward. |

---

## C. Data for genuine sharp-money detection

| Need | Source | Status / cost |
|---|---|---|
| Live odds, many US books | The Odds API `us`/`us2` | **Have it** ($30/mo, 20k credits shared with V3) |
| Market-making / sharp books | The Odds API: Pinnacle (EU region); BetOnline, LowVig (US region) | Have it. Circa availability **unconfirmed**. Pinnacle's own public API is not an option. |
| Historical odds, 5-minute snapshots | Odds API `/historical`: featured markets since **Sep 2022**, props/period markets since **May 2023** | Have it (paid plans only), but **10 credits × markets × regions** per snapshot. See budget below. |
| Opening/closing lines, movement, first mover, steam, key numbers, buyback, outliers | Derived from snapshot sequences (ours + historical) | Feasible. Resolution is limited to how often we snapshot. |
| **Ticket % / handle %** | No source we have provides them. The Odds API does not. Action Network PRO, VSiN (DraftKings splits), Sports Insights / Bet Labs and SportsDataIO publish or sell them. | **Not available.** Scraping would breach most terms of service. Licensed access needs a quote and your approval. Until then, RLM-by-splits and ticket/money divergence return **INSUFFICIENT EVIDENCE**, never modelled values. |
| NFL play-by-play, EPA, success rate, rosters, injury reports | nflverse | Free |
| CFB stats, AP/CFP rankings (Top-25 eligibility) | CollegeFootballData API | Free tier with key; paid tiers available |
| CFB injuries | No reliable free source | Will be flagged **incomplete** on every CFB game |
| Weather | National Weather Service API | Free |

**Credit budget reality** (from the documented formula; confirmed against response headers on the first live call):
- One historical snapshot of 3 markets restricted to ≤10 books costs **30 credits**. About 8 snapshots per week over an 18-week NFL season is roughly **4,300 credits**.
- Hourly snapshots would cost over 90,000 credits per season, which is impossible.

So the plan is:
1. Inventory what V3 already collected (free).
2. Fill targeted gaps only, with a per-backfill cost estimate sent to you for approval.

Every request goes through `edge_v4/credits.py`:
- a V4 monthly cap (default 6,000 of 20,000) and a 24-hour cap;
- a floor that V4 can never push the shared remaining balance below;
- an owner-approval flag on large requests;
- caching, and an immutable usage ledger. V3's usage is inferred from the provider's `x-requests-used` header minus V4's own ledger.

---

## D. Research methodology for positive-EV opportunities

1. **Expect efficiency.** NFL sides and totals at sharp books are among the most efficient betting markets. The most plausible retail edges, in order:
   - **(a)** soft-book prices that lag the sharp no-vig consensus (price shopping / stale lines);
   - **(b)** early-week CFB lines before limits rise;
   - **(c)** key-number pricing errors (3, 7, 10 on spreads), using our exact push probabilities.

   Model-only "we predict better than Pinnacle" edges are the least likely and get the strictest burden of proof.
2. **Pre-registration.** Each strategy is code with a version, registered *before* it is evaluated. The registry counts every strategy tried, and that count feeds the multiple-testing correction (implemented; the tests show the same bets go from SUPPORTED to NOT PROVEN when 200 strategies were tried).
3. **Chronological data split:**
   - 2022–23 seasons: development.
   - 2024: validation.
   - 2025: holdout, opened **once**.
   - 2026 onward: forward paper betting.

   Inside development, use walk-forward weekly expanding windows; the splitter refuses equal-timestamp leakage (tested).
4. **Executable prices only.** A backtest bet uses a recorded quote no more than 15 minutes old at decision time. It fills only if the same price is still posted in the next recorded snapshot; otherwise it is slippage-adjusted or rejected. **Closing odds are never substituted for missing earlier odds.**
5. **Two evidence tracks:**
   - **CLV** (low variance, available early). It is measured only where a close was actually observed; coverage is reported, and a close is never imputed.
   - **Realized ROI** (high variance, the final arbiter).
6. **Sample-size arithmetic** (why this takes time): at −110 the per-bet standard deviation is about 0.95 units. To show a 3% ROI is real at one-sided 95% takes about **2,700 bets**. CLV can show a provisional signal in a few hundred bets.
7. **Engine A's Need is tested as a hypothesis.** It describes our synthetic book's liability, which was built from assumed behaviour. Until behaviour is calibrated to observed data, I expect it to carry no outcome information, and the test will say so.
8. **Benchmarks reported beside every result:** bet-the-market-favourite, random selection, and closing-line baseline.

---

## E. Development environment (isolated) — needs your approval

**Recommended: a new DigitalOcean server in a separate project, with nothing shared with production** (no shared keys, firewall rules, VPC or credentials).

| Item | Est. monthly |
|---|---|
| Server: 8 GB RAM / 4 vCPU / 160 GB SSD (Basic) | ≈ $48 (verify on DigitalOcean's pricing page at checkout; third-party listings disagree on the vCPU count at this price) |
| Weekly backups (20% of server price) | ≈ $9.60 |
| PostgreSQL, Claude Code, website staging (self-hosted) | $0 |
| Private GitHub repository | $0 |
| **New spend** | **≈ $58/month** |
| The Odds API (existing, shared) | $30 (already paid) |

A 4 GB server (about $24/month) would run but is tight for 20,000 bettors, an 8.6 GB database copy, and model fitting.

**One-time setup for you (about 10 minutes):**
1. Create the server and a private GitHub repository, and connect GitHub to Claude, so code persists and both this session and the server session can push.
2. Run the server bootstrap script, which I'll adapt from the existing package: dev user, no sudo, Claude Code Remote Control, PostgreSQL.
3. **Data copy, without using production disk:** one command run from *your* computer streams a read-only, low-priority export from production directly into the dev server. Nothing is written to production's disk, and no new keys are placed on production.
4. **API key transfer:** a pipe from production's environment file straight into a `0600` file on the dev server. The value never appears on screen, in history, in logs, or in chat.

I will write the exact scripts once you approve. After that, no routine commands from you.

---

## F. Phased roadmap

Each phase finishes only when its tests pass.

| Phase | Deliverable | Acceptance test |
|---|---|---|
| **P0** ✅ | Decision core: EV, de-vig, staking, credit gate, validation statistics | 41 tests; 6/6 mutations caught |
| **P1** | Dev server, repository, data import; **data inventory** (coverage by game, book, market and time; gaps such as 25–26 Oct) | Inventory report; zero rows without provenance |
| **P2** | V4 collector behind the credit gate; quality monitors; historical backfill plan with cost | Fixture tests for every monitor; credit ledger reconciles with headers |
| **P3** | Engine C baseline: NFL rating + EPA model and a Top-25 CFB model, producing margin and total distributions with key-number mass | Out-of-sample log loss and Brier vs market baseline; calibration plots |
| **P4** | Engine B: price-only signals (first mover, steam, consensus outlier, key-number crossings, buyback); experimental 0–100 score | Calibration against later CLV; INSUFFICIENT EVIDENCE paths tested |
| **P5** | Engine A at 10k + 10k with totals; Need explained by archetype; calibration hooks | Exposure reconciles to ledger under every outcome |
| **P6** | Walk-forward backtests of pre-registered strategies; edge evidence report | No leakage (enforced); multiple-testing-adjusted results |
| **P7** | Website: all 8 sections, provenance labels on every figure | Browser tests on mobile and desktop widths; read-only database role |
| **P8** | Forward paper betting, deployment and rollback plan | Stops for your deployment approval |

---

## G. Evidence required before claiming a profitable edge

All of the following must hold, for a strategy that was pre-registered:
1. **Out of sample:** at least 2,500 graded bets across the holdout and forward periods, spanning at least 2 seasons. There is no shortcut through a lucky stretch.
2. The lower bound of the ROI confidence interval is above 0, **after** correcting for every strategy tried.
3. Mean CLV is above 0, with observed closes for at least 80% of bets.
4. Probability log loss beats the no-vig market baseline out of sample, if the strategy relies on a model.
5. Results are consistent across seasons and halves; no single week or game drives them.
6. Prices were executable: fills verified against recorded subsequent quotes, and achievable within stated limits.
7. Forward paper results agree with the backtest within its uncertainty interval.

**Public status labels:**
- **NOT PROVEN** (default).
- **PROMISING** (CLV only, provisional).
- **SUPPORTED** (all criteria met).

`edge_evidence_summary()` already implements criteria 1–3 and returns NOT PROVEN unless every one is met.

---

## H. Most serious weaknesses and risks

1. **The likeliest honest outcome is "no durable model edge on main NFL markets."** The realistic profit source is price shopping against sharp consensus. That edge is real but small and capacity-limited.
2. **Execution:** soft books limit winning accounts. A backtest edge that cannot be bet at size is not an edge.
3. **Small samples:** about 272 NFL games a season, and fewer Top-25 CFB games. Proof will take seasons, not weeks.
4. **No betting-splits data.** Two of the fourteen sharp signals cannot be built without licensed data.
5. **Credits:** 20,000 per month shared with V3 limits both historical depth and snapshot frequency.
6. **Synthetic circularity:** Engine A's Need reflects our own assumptions until calibrated. It must never be presented as market evidence.
7. **Single provider:** the Odds API outage of 25–26 Oct left a ~40-hour gap. Closes for games in that window are unobservable.
8. **Production fragility:** the disk is 98% full (see §0).
9. **SIMS is unknown:** it may not be point-in-time verifiable.
10. **Legal and terms of service:** sports betting legality varies by state; scraping restricted sites is excluded.

---

## Decisions needed from you

1. Approve the development server (≈ $58/month) or name an alternative.
2. Create a private GitHub repository and connect GitHub to Claude (free).
3. Set V4's Odds API allocation (default 6,000 of 20,000 per month; I need V3's typical monthly usage to size this safely).
4. Provide the ArbitrEdge SIMS code and any historical outputs.
5. Decide later whether to license betting-splits data; not needed for P1–P3.
