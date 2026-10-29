# Recreational bettor model: assumptions audit and calibration proposal (2026-10-29)

**Scope:** the synthetic sportsbook's simulated customers (`sportsbook_v4/params.py`, `v4_params_m3_unvalidated`; engine m4).

Every number in that file is an unvalidated assumption. None is fitted to observed sportsbook customer data, which ArbitrEdge does not have. This audit says what each assumption does in practice and how it could be calibrated. Modeled splits stay modeled: even after calibration they would be a model that resembles published data, never a representation of any real sportsbook's splits.

## 1. What the live ledger shows

Week of Oct 27, m4 runs, as of Oct 29 10:00 UTC.

| | NFL | CFB (AP Top-25 games) |
|---|---|---|
| Spread wagers / handle | 296 / $48,539 | 573 / $108,364 |
| HIGH_STAKES_REC: share of tickets → share of handle | 3.7% → **60.2%** | 4.9% → **43.2%** |
| SHARP: share of tickets → share of handle | 2.4% → 18.8% | 4.0% → 37.3% |
| REC_CASUAL: share of tickets → share of handle (median stake) | 55.4% → 11.8% ($20) | 56.5% → 12.1% ($20) |
| Top 1% / top 10% of bettors' share of handle | 26% / 84% | 18% / 83% |
| Recreational tickets on the favourite | 58.1% | 56.7% |

**Reading:**
- **Money splits are driven by a few large bettors.** A HIGH_STAKES_REC bettor has a median bankroll of $40,000 and a 3% unit, so a typical stake is about $1,200–2,200. A casual bettor's typical stake is $20.
- So "recreational handle %" mostly measures where a handful of big recreational bettors went, not the broad public. **Ticket %** is the broad-public measure.
- This is why the tracker's no-bettor-above-25% rule and the ticket-majority rule matter. It is also a strong argument for calibrating stake sizes (§4).

## 2. Assumption by assumption

| Assumption | How it works now | Effect | Issue |
|---|---|---|---|
| **Team popularity** | `TEAM_POPULARITY_PRIOR` is empty: every team weighs 1.0 | **Inert.** No team draws more action for being popular (Cowboys, Packers, Chiefs). Lopsided splits come only from favourite/home/loyalty/narrative terms and from where the big bettors happen to land. | The most-cited real driver of public money is missing. Adding it needs a sourced table and a new parameter version. |
| **Recent form** (`recency_weight`) | needs a facts feed; the live book runs with none | **Inert** in live runs | Narrative chasers behave like milder favourite bettors |
| **Primetime** | game-choice weight × (1 + primetime_weight) for standalone games (TNF/SNF/MNF, lone windows); 0.3–1.5 by archetype | Works for NFL. CFB night games were not classified. | Fixed for weeks opening from 2026-11-03 (CFB 7 pm+ ET = primetime) |
| **National TV** | weight × (1 + national_tv_weight) | **Was inert** (never set) | Fixed for new weeks: NFL standalone = national. CFB TV assignments are unknown and not guessed. |
| **Kickoff proximity** | game choice weighted by exp(−hours to kickoff / τ), τ 30–96 h recreational | Bettors prefer games kicking off sooner | Arrival *times* follow day-of-week and hour, not hours-to-kickoff. No last-hours rush. |
| **Favourite bias** | logit term fav_pref × sign(favourite): REC_CASUAL +0.5 (about 62% favourite), NARRATIVE +0.3, HIGH_STAKES +0.4, LOYAL +0.2, CONTRARIAN −0.6 | About 57–58% of recreational tickets on favourites | **Size-insensitive:** a 1-point and a 14-point favourite get the same pull. Published data usually shows the favourite share rising with the spread and with team popularity. |
| **Home bias** | home_pref mean 0 (deliberate, after V3 defect A) | none | Plausible; published data on public home preference is mixed |
| **Overs** | over_pref +0.3 casual, +0.4 narrative | public leans over | Consistent with the commonly reported over bias; magnitude unvalidated |
| **Stake sizing** | stake = bankroll × unit fraction × lognormal noise, capped by the weekly budget and book limit; bankroll lognormal by archetype | Extremely skewed: a 4% archetype holds 43–60% of handle | Real books are also skewed, but the size of this skew is an assumption with large consequences |
| **Archetype shares** | casual 51%, loyal 12%, narrative 12%, selective 10%, contrarian 8%, high-stakes 4%, sharp 3% | defines the "public" | Unvalidated |
| **Market mix** | spread 50–60%, total 15–35%, moneyline 10–35% | — | No parlays, props or live betting, which carry much of real books' handle |
| **Sharps** | bet only when the book's number is worse than the market makers' by ≥ 0.5 pt or 1.5 pp; stake 2% of a $60k median bankroll, up to ×1.5 with edge, limit $3,000 | 19–37% of handle | Real sharp share is unknown and book-specific |

## 3. Consequences for the Need strategies

1. **The Need is driven mostly by HIGH_STAKES_REC and SHARP positions.** C1 and C3 require ticket support and cap single-bettor concentration, which filters out the most whale-driven cases. C2 has no pressure rule, so it will often follow whale positions; that is worth knowing when comparing cohorts.
2. **Big-public games are probably under-represented** while team popularity is neutral. The national-TV fix helps from next week.
3. **None of this changes how Need is computed**: it is always the book's P&L from its accepted wagers. The question is whether the simulated customers resemble real ones, and that is what calibration addresses.

## 4. Calibration proposal (needs owner decisions; nothing changed yet)

**Principle:** fit a small number of interpretable parameters to **published, citable statistics**, then validate on held-out data.

- Each calibrated set becomes a **new `PARAMS_VERSION`** used only by new weekly runs. Existing runs, wagers and settlements are untouched.
- Splits remain labelled MODELED.

| Target statistic | Credible sources | Cost and terms | Parameters it would calibrate |
|---|---|---|---|
| Ticket % and money % on favourites, by spread size; home/away; overs | VSIN publishes DraftKings and Circa splits; Action Network aggregates splits from partner books; academic studies built on SBR/Covers splits (e.g. Humphreys, Paul & Weinbach, and others) | Published pages are free to read, but terms usually forbid scraping. Licensed feeds (e.g. Action Network API, SportsDataIO) are **paid** and need owner approval. A free option: **the owner hand-records** published splits for a sample of games (e.g. 50 per season) for internal calibration. | fav_pref, its dependence on spread size (would need a size term), over_pref, home_pref |
| Team popularity | Free proxies: TV viewership by team, Google Trends, social followers, attendance. Published "most-bet teams" lists from operators. | free; each source cited in the parameter version | a `TEAM_POPULARITY_PRIOR` table |
| Handle concentration (share of handle from the top x% of accounts) | Regulator and research reports, e.g. UK Gambling Commission and academic account-level studies of skew; operator investor materials for average handle per customer | free, public | bankroll and unit distributions, archetype shares |
| Total handle scale and hold by sport and month | State regulator reports (NJ DGE, PA PGCB, NV GCB monthly): NFL/college handle and hold | free, public | overall arrival rates, and a sanity check on simulated hold (realized hold is now measured by settlement) |
| Primetime and national-TV handle share | Operator press releases citing record handle on SNF/MNF, regulator monthly totals | free, anecdotal | primetime_weight, national_tv_weight |
| Kickoff timing | Published intraday handle curves (occasional operator/media data) | free, sparse | arrival timing, and a possible kickoff-proximity arrival term (declined for now) |

**Method:**
- Choose about 6–10 target statistics, simulate past weeks (the `tools_v4/need_history.py` pattern), and fit by method of moments or approximate Bayesian computation.
- Hold out a season for validation and report the fit error.
- Freeze the result as a new parameter version with its sources listed in the file.
- Re-run the historical sensitivity test under it; the result is still exploratory, not proof.

**Decisions needed from the owner:**
1. **Paid licensed splits feed?** It would be the most direct calibration source, but it is a paid service and needs your approval. I do not recommend it before the free options are exhausted.
2. **Manual sampling of published splits** for calibration? This takes your time, not money; I can provide a simple form.
3. **Free public popularity proxies:** OK to build a sourced `TEAM_POPULARITY_PRIOR` for the next parameter version?

Until then, all cohorts stay WATCH/paper, and the bettor model's limits are stated on every modeled view.
