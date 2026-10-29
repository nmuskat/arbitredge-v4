# ArbitrEdge V5: large-scale independent synthetic sportsbook — audit and phased plan (2026-10-29)

| | |
|---|---|
| Status | **Plan only. Nothing here is active.** m4 stays the live engine. |
| Branch | `v5-dev` (non-deploying), cut from `31c2ef3` (m4 + settlement + bankrolls + build-time fix) |
| Not included | The parked calibration commit on `v4-dev-postgame-hold`, which targets published splits, is **not** part of V5 and will not be merged as such. A few neutral pieces of it can be re-used deliberately: the offline model-comparison tool and a team-popularity index with its inputs reframed (§4). |

**Guiding principle:** ArbitrEdge is the house. Independent simulated customers bet throughout the week. We measure where their money piles up, which outcomes hurt or help the house, and whether betting the house's Need has any edge. The goal is not a system built to manufacture winning picks.

## 1. Audit: m4 against the large-sportsbook vision

| Vision requirement | m4 today | Gap |
|---|---|---|
| **Persistent customers, 10k NFL + 10k CFB** | yes: deterministic populations; identities, bankrolls and traits persist across weeks (fixed `PARAMS_VERSION`) | none on persistence. Scale (§2.4) is a separate question. |
| **Individuals, not seven identical groups** | each customer draws their own unit size, betting frequency, favourite/home/popularity/recency leans and a loyal team, from **archetype-specific distributions** | **partial.** Behaviour *rules* are per archetype (timing profile, market mix, price caps, shopping, thresholds); only trait *magnitudes* vary. No customer mixes styles; nobody drifts over time. |
| **Weekly activity / inactivity** | arrivals are a Poisson process at a fixed per-customer weekly rate, shaped by day/hour profiles | **missing:** no inactive weeks, no churn or re-activation, no season-start or holiday surges, no "only bets primetime" habits |
| **Betting frequency and timing** | day-of-week × hour profiles (3 shapes); game choice favours kickoffs within 30–96 h | **partial:** arrivals aren't kickoff-relative (no last-hours rush); timing doesn't react to news |
| **Stake sizes and bankrolls** | stake = bankroll × unit × lognormal noise, capped by a weekly budget (a fraction of bankroll) and book limits; integer dollars | **partial:** bankroll is the *opening* bankroll forever. Settlement now computes current bankrolls, but the engine doesn't use them. |
| **Market preferences** | spread / total / moneyline weights per archetype | **missing:** parlays (a large share of real books' handle and hold), props, teasers, live betting |
| **Line shopping, price sensitivity** | price caps (e.g. won't lay worse than −130); price-sensitive archetype and sharps compare against market makers | **partial:** shopping is binary per archetype; no sensitivity curve (fewer bets as the price worsens) |
| **Favourite / underdog / team preferences** | fixed logit leans; loyal-team boost; popularity table **empty** (inert) | **partial:** favourite pull ignores spread size; no regional fan bases; popularity is inert |
| **Primetime / national TV** | game choice weighted for standalone games; national TV flagged from 2026-11-03 | mostly fixed for new weeks; college TV windows unknown |
| **Skill and discipline** | "skill" exists only as sharps (edge rule) and selective bettors (conviction threshold) | **missing:** a continuous skill axis; discipline (stop-loss, staking consistency) |
| **Emotion and narrative** | recency/narrative weights exist but need a facts feed that live runs lack (inert) | **missing in live:** recent form, rivalries, revenge spots and similar narratives |
| **Responses to wins, losses, bankroll** | none | **missing:** loss chasing, house-money effect, tilt, busting out, reloads, quitting |
| **Independent decisions: whether, when, how much** | yes: each arrival independently chooses a game, market, side and stake, or passes | ok, but "whether" is only a price/budget check; there is no mood/activity state |
| **Accept at the book's offered price, using real odds as input** | the book offers a representative real-market quote (median line, one real book's price pair) with a 10-min lag; after sharp action it copies market makers | **partial:** the "house price" is a real book's price, not ArbitrEdge's own. No house vig policy, no shading toward liability, no line moves from its own book. |
| **Real vs hypothetical prices** | each wager stores its price, price book and quote references | **partial:** there is no separate "house quote" record distinct from the observed market quote |
| **Preserve every wager permanently** | yes: append-only table, immutable hash-verified release files, replay verification | none |
| **Settle and carry bankrolls forward** | settlement + bankrolls built (cent-exact, corrections superseding); deploys after tonight's game | carry-forward **into behaviour** (stakes, busts, reloads) is missing |
| **Pricing, limits, risk controls** | flat max wager $10k, sharps $3k; min $5 | **missing:** per-customer limits that react to results (profiling/limiting winners), liability-based line moves, max-payout caps, a game-level risk budget |
| **Exposure per game and market: handle, % tickets/handle, rec vs sharp, unique bettors, concentration, potential payouts, P&L for every outcome, max downside, Need** | yes: exact P&L over every final margin/total/score from each wager's own number (`settlement.exposure`, `joint_book`); pressure metrics in the Need tracker | none for straight bets |
| **Need from payout obligations, not fading the majority** | yes: Need is the side whose cover leaves the book better off, from actual payouts | none |
| **Combined exposure: spreads + totals + moneylines + correlated parlays** | `joint_book` combines a game's three markets over every exact score | **missing:** parlays, cross-game portfolio exposure, the week's portfolio P&L distribution |
| **Independent of published splits** | yes: m4 never reads any split source | none (the parked calibration branch is not merged) |
| **Validation: cohorts, ATS, ROI, CLV, intervals, sensitivity** | four frozen cohorts; tracker with ATS / ROI / CLV / CIs / Holm; four sensitivity variants for splits | **partial:** sensitivity covers splits, not **whether the Need side flips** under alternative populations |
| **Scale and cost** | 20,000 customers; each dashboard build re-simulates the whole week (verification plus 4 variants) | **constraint:** build time grows with customers × week × variants. Free-tier minutes cap how far V5 can scale (§2.4). |

**Summary:** m4 already has the house's books right: immutable wagers, exact per-outcome P&L, Need from payouts, settlement. What's missing is mostly the **customers' lives** (activity, skill, emotion, bankroll feedback) and the **house's own behaviour** (pricing, limits, risk), then parlays and portfolio exposure.

## 2. Design principles for V5

1. **Emergent, never targeted.** No ticket share, handle share or Need is a model target. Parameters describe individual behaviour; splits and Need are outputs.
2. **Individuals with state.** Each customer carries persistent latent traits *and* evolving state (bankroll, recent results, tilt, activity), so two customers with the same "type" diverge over a season.
3. **Every assumption is listed** with its basis (research, operator practice, or "assumption; not independently verifiable"), and sensitivity-tested for its effect on Need.
4. **Accepted wagers are immutable**; any new behaviour starts with a new weekly run under a new engine/parameter version. m4 runs, cohorts and settlements are never touched.
5. **Free-tier first.** V5 must fit GitHub Free minutes and the Cloudflare limits. That requires incremental simulation (§2.4) before any scale increase.

### 2.4 Scale and cost

- Today each build replays the whole week to verify the ledger, plus four sensitivity variants (now cached 3 h).
- V5 should **advance from a verified checkpoint**: keep the population state and weekly totals, simulate only new arrivals, and verify by full replay once a day (one job, ~2 minutes) instead of every build.
- With that, 20k customers with stateful behaviour should stay under a 1-minute build.
- Scaling customers further (e.g. 100k for a "large book" feel) is possible only after measuring it. **20k remains the plan unless you approve otherwise.**
- "Large sportsbook" is achieved through realistic behaviour and handle distribution, not raw headcount. Dollar amounts can be reported as both raw and per-1,000-customers so scale is explicit.

## 3. Behaviour model for V5: what research informs and what cannot be verified

| Behaviour | Basis | Verifiable? |
|---|---|---|
| Heavy-tailed activity and stakes: most accounts bet rarely and small; a few bet often or big | account-level studies of online sports bettors (e.g. LaBrie, LaPlante, Nelson, Schumann & Shaffer 2007, bwin data); operator disclosures that a small share of customers produce most handle | shape: published; exact parameters for a US NFL book: **no** |
| Activity decay, churn, re-activation; seasonal starts | same longitudinal account studies; industry descriptions of NFL-season sign-ups | shape only |
| Favourite and over leans; preference for "winning" teams | Levitt 2004 (books shade toward bettor biases); Paul & Weinbach; Simmons & Nelson (intuitive favourite bias); Shank 2019 (non-linear by spread) | direction: yes; size: **no** |
| Loss chasing; house-money effect; breaking even | prospect theory (Kahneman & Tversky); Thaler & Johnson 1990; gambling-behaviour literature on chasing | qualitative; sizes **assumed** |
| Price sensitivity (fewer bets as prices worsen) | basic consumer demand theory; sportsbook promotions literature | **assumed** |
| Skilled minority; books limit winners | market-efficiency literature (closing lines are hard to beat); widely reported operator practice of limiting sharp accounts | qualitative |
| Primetime / national TV draws more casual action | operator press releases on record SNF/MNF handle; viewership data | direction only |
| Team popularity and regional fans | viewership, attendance and search interest as **attention** proxies (not betting shares) | proxy only |
| Parlay share and hold | state regulator monthly reports (e.g. Illinois, Pennsylvania, New Jersey) publish parlay handle and hold for some states | aggregate: yes |

Parameters that remain assumptions are stated as such and covered by §5.6 sensitivity.

## 4. Phased implementation plan

Each phase is built and tested on `v5-dev`, evaluated **offline** against m4 on the same real weeks (the shared replay harness), and reported to you. Nothing activates without your approval. Activation always starts a new Tuesday week.

### Phase 1 — Persistent customer activity and realistic participation
- **Customer state store:** per customer and per week (active or inactive, weeks since last bet, season-to-date activity), immutable weekly snapshots like `v4-book`.
- **Participation model:** a weekly activation probability from a persistent engagement trait, decaying with inactivity, boosted by primetime/national games and early-season interest. Within-week arrival intensity mixes day/hour habits with a **kickoff-relative component**; that component is off by default, your earlier choice, and offered as a sensitivity variant.
- **Mixed styles:** archetypes become *priors*. Each customer has continuous traits (favourite lean, discipline, skill, narrative susceptibility, price sensitivity, loyalty to one or two teams), drawn from correlated distributions so types blur.
- **Acceptance:**
  - weekly active-customer share and its distribution are reported;
  - no hard-coded per-team or per-game numbers;
  - replay determinism and m4 byte-identity both hold.

### Phase 2 — Individual decisions and stake sizing
- **Choices:** game (attention: primetime, popularity proxies, loyal teams, kickoff proximity); market (personal mix); side (personal leans, a spread-size-dependent favourite curve, narrative signals from **real** data such as last week's results from the ingested final scores); and "pass" based on price (a sensitivity curve, not a cap).
- **Stake:** bankroll × personal unit × confidence, bounded by personal discipline and the weekly budget. It uses the **current** bankroll from settlement (it was static before).
- **Acceptance:** split and concentration distributions are reported; no target matched. Sensitivity: Need-side stability (§5.6).

### Phase 3 — Long-term bankroll and performance histories
- **Engine reads settled bankrolls** at each week's open, from a frozen bankroll snapshot whose hash is part of the run identity (`SETTLEMENT_BANKROLL_PLAN` §3).
- **Behavioural responses**, each behind its own parameter and each sensitivity-tested:
  - loss chasing (bigger, more frequent bets after losses);
  - house money (looser after wins);
  - tilt after bad beats;
  - busting out below the minimum stake;
  - reloads (a fraction re-deposit after a gap);
  - quitting after drawdowns.
- **Customer histories:** wagers, results, bankroll path and lifetime P&L, browsable on a "Customers" view (MODELED).
- **Acceptance:**
  - bankroll = opening + Σ settled ± reloads, to the cent;
  - realized hold reported;
  - busts and reloads counted.

### Phase 4 — Sportsbook pricing, limits and risk management
- **House quote, separate from observed market quotes.** House price = real-market consensus (input) + a house vig policy + optional, bounded liability shading.
  - Shading is the book moving its *own* number when its exposure is lopsided, within limits relative to the market; it is never applied to past wagers.
  - Every wager records both the house quote and the market quote it was based on.
- **Limits:**
  - per-customer maximums that tighten for customers whose results or closing-line value look sharp (profiling, as real books do);
  - a max payout per wager;
  - a game-level risk budget that reduces limits when the house's downside is large.
- **Sharp response:** respected action still moves the house number toward the market makers (m4 behaviour), now with logged reasons.
- **Acceptance:**
  - house vs market price always distinguishable;
  - limit actions logged;
  - accepted wagers never re-priced (tested).

### Phase 5 — Accurate game-level and portfolio-level liability
- **Straight bets (exists):** exact P&L for every outcome per market and joint per game. Extended with potential-payout totals, maximum downside and the outcomes that benefit the house, all already computed from payouts.
- **Parlays** (new market, a modest share of casual tickets; parameterised from regulator parlay data as a share and hold check, never a target):
  - **Exposure enumerated exactly** over leg outcomes. A parlay pays only if every leg wins, so its liability attaches to the *joint* outcome of several games.
  - **Same-game parlays:** exact over the game's score grid (spread, total and moneyline legs are functions of the same score), like `joint_book`.
  - **Cross-game portfolio:** the house's week P&L is a function of all games' outcomes. Exact enumeration of every combination is infeasible, so:
    - (a) **deterministic per-game views:** the house's P&L for each outcome of one game with every other game held at each of its outcomes, giving the range;
    - (b) a **probabilistic portfolio distribution** using game-outcome probabilities from the observed closing market (no-vig), not from a picks model. This gives expected house P&L, value at risk and each game's **marginal Need**: the outcome of that game that most improves expected week P&L once parlay links are counted.
- **Need definitions** are reported side by side: straight-bet Need (exact, as today) and portfolio marginal Need (market-probability-weighted). They can disagree; the disagreement is shown, never resolved by preference.
- **Acceptance:** Σ customer P&L = −house P&L for every enumerated outcome; portfolio simulation reconciles with straight-bet exact results when parlays are excluded.

### Phase 6 — Independent prospective validation
- **Existing cohorts C1–C4 keep their frozen rules** and continue on m4 for as long as m4 is live; their records are never re-evaluated.
- **V5 gets its own pre-registered cohorts** (rules frozen before its first eligible game), recorded in V5 snapshots, graded with the same machinery: ATS, recorded-price ROI, CLV, sample sizes, confidence intervals and Holm adjustment.
- **"All Need sides"** remains the baseline for both engines.
- **Sensitivity of Need** (new): for each sealed decision, re-simulate the week under a pre-defined set of alternative populations (activity, stake tails, favourite strength, sharp share, parlay share). Report **Need stability**: the share of variants agreeing with the baseline Need side, and the swing range. Questions to answer: do stable-Need games perform differently from fragile ones (pre-registered as a cohort split)? Is Need driven by a few customers?
- **Engines in parallel:** if you approve, V5 runs as a **shadow** book (its own ledger, release `v5-book`) alongside m4 for a few weeks, with no effect on m4 and only within the minutes budget. Only then would you decide whether V5 replaces m4 for new weeks.
- **Never claimed:**
  - that the modeled Need is DraftKings', FanDuel's or any real book's liability;
  - that betting the Need is profitable without the evidence bar (rebuild plan §G).

## 5. Order, size and cost

| Phase | Main deliverables | Est. effort | Actions-minutes impact |
|---|---|---|---|
| 0 (first) | V5 harness: engine v5 package beside m4, shared replay and evaluation tool, checkpointed incremental advance | 1 session | reduces minutes (incremental builds) |
| 1 | activity, mixed traits, kickoff-relative option | 1–2 sessions | neutral |
| 2 | decisions and stake from current bankroll, price-sensitivity curve | 1–2 sessions | neutral |
| 3 | bankroll snapshots, behavioural responses, customer histories | 2 sessions | small |
| 4 | house quote, vig, shading, limits, risk budget | 2 sessions | neutral |
| 5 | parlays, same-game exact exposure, portfolio simulation, marginal Need | 2–3 sessions | +~0.2 min per build for the portfolio simulation (sized to fit) |
| 6 | V5 cohorts, Need-stability sensitivity, shadow book | 1–2 sessions | shadow book about +50–100 min/month; fits only with Phase 0's savings; to be measured first |

**Costs:** no new API spending (inputs are the Odds API data already collected, nflverse and CFBD scores). No paid services. Cloudflare deployments unchanged. GitHub minutes are kept within the free plan; any phase that would exceed the ~1,750-minute target is reported before activation.

## 6. Decisions requested

1. **Approve the plan and the order** (Phase 0 harness first, then 1 → 6), or re-prioritise.
2. **Customer count:** stay at 10k + 10k (recommended until incremental builds are measured), or plan a larger population.
3. **Shadow running:** may a V5 shadow book run alongside m4 once Phases 0–2 pass offline evaluation, within the minutes budget? It would be its own ledger, with no effect on m4.
4. **The parked calibration branch:** keep it parked (default), or delete its published-split collection parts. Its observation tooling will not be used as a V5 target either way.

V5 is not activated until you explicitly approve it.
