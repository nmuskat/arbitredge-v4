# EXPANDED odds-collection tier: design, projections, approvals needed (2026-10-29)

**Status: built and tested, NOT active.** It runs only when the owner sets `V4_TIER=expanded`. Until then, its extra schedule entries are skipped by the workflow (0 Actions minutes) and nothing about current spending changes.

## 1. Design

**Costs and scope:**
- One fetch costs 3 credits and returns every game of the sport.
- Priority games: every NFL game, plus AP Top-25 CFB games. A game whose ranking can't be determined also counts.
- Closing lines (a fetch at most 45 minutes before kickoff) are taken for every priority kickoff in every tier.

**Kickoff-relative cadence** (`collect_v4/schedule_policy.py`, tier `expanded`):

| Before the next priority kickoff | Minimum gap | Purpose |
|---|---|---|
| final 90 min | 15 min | closing line, inactives, late steam |
| final 6 h | 30 min | late movement, steam (15-minute runs on game days) |
| final 24 h | 1 h | movement and CLV paths |
| final 72 h | 2 h | injury and news reaction |
| final 7 days | 4 h | openers and early moves |

**Calendar windows** (UTC; active only if a priority game is within 5–9 days):

| Window | Sport | Gap |
|---|---|---|
| Sun 22:00 – Mon 08:00 | NFL openers (next week's lines post Sunday night) | 1 h |
| Sun 17:00 – Mon 06:00 | CFB openers | 1 h |
| Mon–Tue 12:00 – 24:00 | both, early-week sharp moves | 2 h |
| Wed–Fri 18:00 – 24:00 | NFL injury-report periods | 1 h |

**Waste controls:**
- **Quiet backoff.** If under 3% of a sport's quotes changed at its last fetch and no priority kickoff is within 24 hours, cadence gaps double. Closes are never delayed. Real data shows about 2–3% of quotes change per hour, so quiet early-week hours back off.
- **No games, no polling.** With no priority game within 7 days, the only fetch is a daily discovery fetch.
- **Piggyback.** A run already fetching one sport also fetches the other if its last fetch is at least an hour old and it has a game within 7 days. This adds credits but no Actions minutes, the scarcer resource.

**Cron grid:**
- The existing grid stays: 3-hourly, plus 15-minute game-day windows including Sunday 12:00–23:45.
- Expanded adds an hourly run (minute 52) in every hour without a 15-minute window, except 07:00–11:59 UTC (2–7 am ET). That's 276 runs a week instead of 176.

**Dashboard rebuilds (Expanded only):** at most hourly, but always after a closing-line fetch.

## 2. What it improves

| Analysis | Test / Balanced | Expanded |
|---|---|---|
| First mover (needs observations at most 1 h apart) | mostly "timing unresolved" | resolvable most of the week (hourly grid) |
| Steam (books moving within 15 min) | game-day windows only | game-day windows and the final 6 h on game days; early-week steam still not observable (see option C) |
| Line movement and CLV paths | ~17 NFL snapshots in the final 72 h | ~70 |
| Synthetic sportsbook | book open only ~6 h after each sparse fetch (6-hour quote freshness rule) | real prices less than 6 h old almost all week, so simulated customers can bet throughout the week |

## 3. Projections

Simulated on the **actual upcoming schedule**: real collector state, real stored games, the current AP poll, the real cron grid, and a 40-minute GitHub start delay. Seven days from 2026-10-29 02:00 UTC (`tools_v4/consumption_check.py`).

| Tier | Credits/7 days | **Credits/month** | Peak calendar day | Worst rolling 24 h | NFL snapshots per game: final 72 h / 24 h / 6 h / 90 min (median) | Top-25 CFB (same) | Closes |
|---|---|---|---|---|---|---|---|
| test (current) | 81 | ~350 | 33 | — | 7 / 4 / 3 / 1 | 13 / 10 / 4 / 1 | all |
| balanced (approved) | 174 | ~756 | 51 | — | 17 / 10 / 5 / 2 | 21 / 15 / 6 / 2 | all |
| **expanded** | 858 | **~3,730** | 186 | **198** | 71 / 34 / 18 / 6 | 78 / 44 / 19 / 6 | all |
| expanded, quiet backoff active (2% change/fetch) | 768 | ~3,340 | 183 | — | 68 / 34 / 18 / 6 | 75 / 44 / 19 / 6 | all |

- The worst single hour is 15 credits.
- A 3-week synthetic test with an international game also passes: every NFL and Top-25 close observed; 2,500–5,000 credits a month; rolling 24 hours at most 250.
- **V3 reserve:** the provider shows 19,940 remaining (Oct 29). At about 3,700 for V4 plus V3's recent pace of about 11 a day, the 2,000 floor is far away. It is still enforced before every request (section 5).

## 4. GitHub Actions minutes: the real constraint

Each job is billed at least 1 minute. Measured: a collect run takes ~10 s, a dashboard build ~55 s, a CI run ~146 s (3 minutes).

| Tier | Collect runs | Dashboard builds | Analysis and rankings | CI (estimate) | **Total/month** |
|---|---|---|---|---|---|
| balanced | 766 | 226 | 40 | 300 | **~1,330** |
| expanded | 1,201 | ~465–530 | 40 | 300 | **~2,000–2,070** |

- **GitHub Free includes 2,000 private-repo minutes a month.** Expanded as configured is at or over that limit.
- **If minutes run out, GitHub stops running workflows until the next month: collection halts.** (On Free, with no spending limit set, nothing is charged.)
- The owner's plan and current usage are visible at GitHub → Settings → Billing and plans → Usage. This environment can't read them.

**Options** (owner chooses; none applied):
- **A — Fit Free.** Rebuild the dashboard at most every 2 h except after closes (about −230), and skip CI for documentation-only commits (about −100). Total about 1,700–1,750; the dashboard can lag the latest odds by up to 2 h, and its labels say so.
- **B — More minutes.** GitHub Pro includes 3,000 minutes. That is a paid plan, so it needs explicit approval under the "no paid services" rule. Not recommended unless A proves too tight.
- **C — Optional steam add-on.** 15-minute runs Sunday 23:00 – Monday 03:00 UTC for NFL openers steam: about +70 minutes and about +100 credits a month.

## 5. Budget safety (live now for every tier)

- **Fresh balance before every paid request.** The gateway calls `/v4/sports` (0 credits; the provider reports `x-requests-last: 0`) to read the live balance. It refuses if the call fails, returns an error, or lacks the balance header.
- **Bug fixed: the V3 floor was skipped at the start of each month.** The check used the last *recorded* balance, which can be hours old, and none exists until the month's first request, so the floor check was skipped entirely. It now fails closed.
- **Bug fixed: stale balance on equal timestamps.** Two ledger rows with the same timestamp could return the older balance; there is now a deterministic tiebreak.
- **Unchanged:** monthly cap, rolling-24h cap, per-request cap (10), V3 floor (at least 2,000) and circuit breaker. A refused request is logged; collection continues next run.

## 6. Proposed variable changes (owner sets them only after approving)

| Variable | Now | Proposed for expanded | Why |
|---|---|---|---|
| `V4_TIER` | balanced (approved) / unset | `expanded` | activates the tier and its hourly runs |
| `V4_MONTHLY_CAP` | 1000 (recommended for balanced) | **5000** | ~3,700 projected, ~35% headroom; the code's hard maximum stays 6,000 |
| `V4_DAILY_CAP` | 60 | **250** | worst rolling 24 h projected at 198; 60 would refuse most Saturday and Sunday fetches |
| per-request cap | 10 | unchanged | a fetch costs 3 |

**Provider limits.** At most 4 requests per run (2 balance checks and 2 fetches), and at most 15 credits in any hour. That's far below any rate limit; a 429 already triggers a cooldown.

## 7. Sharp-money integrity

Denser observations only feed the existing evidence rules:
- moves measured at market-making books;
- timing claims only when the observations are close enough;
- sport-specific thresholds;
- "INSUFFICIENT EVIDENCE" otherwise.

Modeled sportsbook splits are never used as evidence of real sharp action, and are never presented as market splits. Real splits remain UNAVAILABLE (no licensed source).
