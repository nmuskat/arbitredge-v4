# Full-season trading desk replay: Phase 1 feasibility audit (2026-11-11)

**Scope:** completed 2026 games, NFL weeks 1–9 (136 games) and CFB AP Top-25 weeks 0–10 (199 games).
**Status:** no replay was run, no rules were written and nothing changed. The frozen TNF pilot
(`TNF_2026_W10_WAS_NYG.*`) is untouched. Production is still `v4-dev` 8c003b8.

## Verdict for Phase 1
**The trading desk cannot be replayed faithfully.**
- **The decisive problem is that no frozen desk decision rule exists.** The TNF desk opinion was a written judgment
  (evidence list plus reasoning) with no thresholds, weights or formula. Any replay would require writing rules now.
  The analyst writing them has already seen 2026 outcomes in detail (forensic, NEED, bettor and V7 studies of this
  season), so the rules would be in-sample by construction.
- **Several desk inputs were never recorded before kickoff:** weather forecasts, news and QB reports, and SIMS
  probabilities.

If the replay were run as specified, the honest label would be **AUDIT NOT RELIABLE DUE TO DATA LIMITATIONS**.

## 1. Inventory of pregame data

| Input | What exists | Genuinely pregame? | Coverage | Verdict |
|---|---|---|---|---|
| Odds (spread/total, 10 books) | Market store: live collection since 26 Oct, plus the historical backfill (provider snapshots, retrieved on 10 Nov) | **Yes.** Every quote carries the provider's snapshot time before kickoff; backfill snapshots are real provider records, retrieved later but not altered. | NFL: Pinnacle at T−1h for **136/136**; T−24h 91/136. CFB: Pinnacle at T−1h **172/199** spreads and 192/199 totals; T−24h 123/199. | Trustworthy |
| Team efficiency (EPA, success, pace, line proxies) | nflverse play-by-play 2026 (23,822 plays, weeks 1–9); cfbfastR play-by-play 2026 (226,709 plays, EPA, weeks 1–10) | **Yes, if filtered to games before each kickoff.** Per-play rows can be cut point-in-time. Values include later stat corrections (small). | Complete | Trustworthy with point-in-time filtering |
| NFL injuries | nflverse weekly reports (practice status plus game designations), weeks 1–10 | **Probably.** Final designations are published before games, but the file has **no publication timestamps**. It holds each week's last version only (the TNF pilot saw designations missing on Wednesday). | All NFL weeks | Usable with a caveat |
| NFL personnel | Rosters (weekly, with status), depth charts (timestamped, March–November), snap counts | Depth charts **yes** (timestamped). Weekly roster status **uncertain** (no timestamp within the week). Snap counts are post-game. | All weeks | Partly usable |
| CFB injuries / personnel | No injury data; season-level rosters only | — | **Missing** | Not available |
| Weather | nflverse temperature/wind for **45 of 136** completed NFL games: observed game-time values, recorded afterwards. No forecasts were ever stored (the pilot's forecast sources are blocked here). No CFB weather. | **No** (observed, not forecast) | NFL 45/136, CFB 0 | Not available pregame |
| News (QB status, reports) | Not archived | Today's web search returns post-game knowledge | — | **Leakage risk; not usable** |
| SIMS outputs | Ledger entries (hashes only) for pregame forecasts: **NFL weeks 7–9 (43 games), CFB week 8 (17 games)**, created before kickoff | Yes for those games, but the **probabilities are not accessible**: they sit in the SIMS project's own database, the ledger stores only hashes, and no web export was ever published | 60 of 335 games, values unavailable | Not available here |
| SIMS re-runs | The model could be re-run for earlier games | **No.** Model 1.7.2 was approved on 29 Oct after evaluation on 2026 games (`nfl26dev` in the SIMS accuracy report); versions changed weekly (1.5 → 1.6 → 1.7.1 → 1.7.2 → 1.7.2+debut2025b). Re-runs are in-sample. | — | Retrospective only |
| Financial NEED, genuine | Need-tracker records frozen before kickoff (releases v4-need-2026-W44…W46) | **Yes** | **NFL 29 games, CFB 35 games** (live book since 26 Oct; engine r2, plus 13 m4 records) | Trustworthy for those games only |
| Financial NEED, replay | Production-engine replay of earlier weeks (identical engine; quotes only, no results used) | Outcome-free, but retrospective: engine r2 was built on 30 Oct, after weeks 1–7 were played and studied | All games | Usable only as an in-sample reconstruction |
| Lines in the free nflverse schedule | spread_line / total_line | **No.** These are closing lines; the SIMS "market" block reads them | — | Must never be a pregame input |

## 2. Missing or retrospective data
- **Retrospective:**
  - backfill odds (retrieved later, but real pregame snapshots);
  - NEED for every game before 26 Oct (replay);
  - any SIMS re-run;
  - recorded weather (observed at the game).
- **Missing:**
  - weather forecasts;
  - pregame news;
  - CFB injuries and personnel;
  - SIMS probabilities;
  - injury-report publication times.

## 3. Can the desk be replayed with genuine pregame information only?
- **The method can't be.**
  - There is no codified rule to replay.
  - Two of the pilot's inputs (QB/injury news and weather) have no pregame archive.
  - SIMS pregame values are inaccessible.
- **What could be replayed point-in-time without leakage** is a narrower, codified "efficiency + availability"
  desk. It would use play-by-play filtered to earlier games, timestamped depth charts, weekly injury designations
  (with the timing caveat), and odds. But its rules would be written now, after 2026 outcomes were seen.

## 4. Information leakage found or possible
1. **Season-to-date statistics:** none, *if* every statistic is recomputed from plays of games that kicked off
   earlier. The TNF pilot did this correctly (weeks 1–9 for a week-10 game). Any pre-aggregated season table would
   leak.
2. **nflverse schedule lines:** these are **closing** lines. Using them as "the line at decision time" leaks.
3. **Recorded weather:** observed at the game, not forecast.
4. **Weekly roster status / injury files:** the latest version of each week, with no timestamps, so small timing
   leakage is possible.
5. **Re-run SIMS:** model selection used 2026 games (in-sample).
6. **NEED replay:** engine designed after the games; its parameters are documented as not fitted to outcomes, but it
   cannot be called out of sample.
7. **Analyst knowledge:** this project has examined 2026 outcomes extensively (team results, side-type results such
   as "CFB underdogs covered", and every NEED study). Any judgment-based desk replayed over 2026 is contaminated.

## 5. Were the desk's rules developed using 2026 outcomes?
- **No numerical rule was fitted to 2026 outcomes.** The pilot desk used generic football evidence (efficiency,
  success rate, recent form, QB samples, injuries, line movement).
- **But no frozen numerical rule exists either,** and the person or analyst exercising judgment had seen 2026
  results. So it cannot be shown to be independent of 2026 outcomes, and any rules written now would not be.

## Options for your review (nothing started)
- **A. Stop here.** Record "AUDIT NOT RELIABLE DUE TO DATA LIMITATIONS" for a full-season desk replay.
- **B. Codify, then test properly.**
  - Write the desk as fixed numerical rules: point-in-time efficiency, depth-chart starters, weekly designations;
    no news, no weather. Freeze them with a checksum **before** any test.
  - Run them over 2026 **labelled exploratory in-sample**.
  - The real test is prospective, from NFL week 10 / CFB week 11 onward, under a frozen specification.
- **C. Out-of-season check for B's rules:**
  - 2025 play-by-play and injuries are available, but our collection holds **no 2025 odds**;
  - only nflverse closing lines exist, with no prices;
  - so ATS against closing lines at an assumed −110 is possible, but CLV and real prices are not.
- **D. SIMS.** Ask the SIMS session to export the official pregame forecasts it already holds for NFL weeks 7–9 and
  CFB week 8 (read-only; verifiable against the ledger hashes). That is the only genuine SIMS history.
- **Genuine NEED history** (29 NFL / 35 CFB games) can be graded now as a small, honest sample, separately from any
  desk work.
