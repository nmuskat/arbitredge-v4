# V4 data sources: audit and provenance

| Source | What | Used for | Access / cost | Licence status | Limits |
|---|---|---|---|---|---|
| nflverse `nfldata/games.csv` | NFL schedule, results, one spread/total/moneyline per game (2006+ with prices), rest, roof, starting QBs | Engine C training targets; **benchmark** lines | Free, raw.githubusercontent.com | No licence file in the repository. Internal research only; do not redistribute or display its lines as ArbitrEdge market data | Line book and timestamp unspecified (approx. near-close consensus). **Not an executable price.** No opener, no movement. |
| nflverse-data `pbp` release | Play-by-play with EPA and success (2006–2026) | Team efficiency features | Free, GitHub releases | nflverse data; attribution required; confirm before public redistribution | Updated after games; refresh weekly in season |
| The Odds API (existing $30 plan) | Live multi-book odds; historical 5-minute snapshots (featured markets from Sep 2022; props from May 2023) | Executable prices, line movement, closes, sharp signals | 20k credits/month shared; V4 capped at 6,000 by the gateway | Commercial subscription | Historical data costs 10× credits; no ticket or handle splits |
| V3 production database (copy) | Collected multi-book snapshots | Price history at no credit cost | Needs the copy step (blocked: no dev server) | Own data | Coverage unknown until inventory; gap 25–26 Oct 2026 |
| CollegeFootballData API | CFB games, lines, rankings | CFB Engine C, Top-25 filter | Free tier with key | Terms to review | **Blocked from this sandbox** (egress refused) |
| cfbfastR-data `data/betting` | CFBD-derived CFB lines (daily) | CFB benchmark | Free, raw.githubusercontent.com | Derived from CFBD; terms to review | Not yet ingested |
| Betting splits (Action Network, VSiN, SportsDataIO …) | Ticket and handle % | Splits-based sharp signals | Paid / licence needed | — | **Not available.** Those signals return INSUFFICIENT EVIDENCE |

Every downloaded file is recorded in `data/raw/MANIFEST.json` with URL, SHA-256, size, fetch time and licence note. Raw data is not committed to git; `python -m data_v4.fetch_nflverse` re-creates it.
