# EXPANDED odds-collection tier: design, free-plan fit, cost report (2026-10-29)

**Status: built and tested, NOT active.** It runs only when the owner sets `V4_TIER=expanded`. Until then:
- its extra schedule entries are skipped by the workflow (0 Actions minutes);
- nothing about current spending changes.

Requirement (owner, 2026-10-29): GitHub and Cloudflare stay **$0/month**.

## 1. Design

**Costs and scope:**
- One fetch costs 3 credits and returns every game of the sport.
- Priority games: every NFL game, plus AP Top-25 CFB games (at least one ranked team). A game whose ranking can't be determined also counts.
- Closing lines (a fetch at most 45 minutes before kickoff) are taken for every priority kickoff in every tier, international NFL games included (Sunday-morning window).

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
- **Quiet backoff.** If under 3% of a sport's quotes changed at its last fetch and no priority kickoff is within 24 hours, cadence gaps double. Closes are never delayed.
- **No games, no polling.** With no priority game within 7 days, the only fetch is a daily discovery fetch.
- **Piggyback.** A run already fetching one sport also fetches the other if its last fetch is at least an hour old and it has a game within 7 days. This adds credits but no Actions minutes.

## 2. Fitting GitHub Free (changes made 2026-10-29)

Every scheduled run is one job, billed at least 1 minute even when nothing is due. Every dashboard rebuild is another job and one Cloudflare deployment.

**Cron grid (`collect_v4/cron_plan.py`).** Expanded runs **238** times a week, down from 276:
- Base grid (all tiers): 3-hourly runs, plus 15-minute game-day windows including Sunday 12:00–23:45 UTC.
- Expanded adds an hourly run (minute :52) in hours without a 15-minute window, except:
  - 07:00–11:59 UTC (2–7 am ET);
  - Tuesday and Wednesday before 18:00 UTC. No NFL kickoff falls within 24 h there, so the 2–4 h cadence gaps mean an hourly run almost never fetches: about 3 of 26 runs a week, simulated on the real schedule. The evenings stay hourly (early-week moves and the Wednesday injury report).
- **Redundant 3-hourly runs skipped under Expanded** (`EXPANDED_SKIPS_BASE`): 14 a week, Mon–Fri. Each sits 25 minutes after an hourly run, outside any final-6-hour period. Thursday and Friday evenings, Saturday and Sunday keep them. Other tiers run them as before.
- **Bug fixed: duplicate runs.** The 3-hourly entry matched the same minute as a 15-minute window 12 times a week. GitHub starts one run per matching entry, so that was two billed runs for one slot. The entries no longer overlap (tested).

**Dashboard rebuilds (`schedule_policy.site_due`, all tiers):** only after a run that saved new odds, and then:

| Situation | Rebuild |
|---|---|
| quiet period | at most every 2 h |
| within 6 h of a priority (NFL / Top-25 CFB) kickoff | every 45 min |
| closing-line fetch | always, immediately |
| no new odds | never |
| monthly self-limit: 380 builds | only after closes, otherwise every 6 h |
| monthly self-limit: 440 builds | none until the next month (the page labels its odds STALE) |

The page always shows when its odds were observed. LIVE, DELAYED and STALE are computed in the browser from that time (45 min / 6 h).

**Dashboard build made 2.3× faster, with identical output.** Measured locally on the real data: 69 s → 30 s.
- Simulated customers arriving between two odds updates now share one price lookup.
- The bettor population is generated once per parameter set (the sensitivity variants are kept separate).
- The score-distribution and fair-line calculations reuse identical results.
- Verified byte-identical: the page, its data file and every ledger wager, plus tests including mutation checks.
- A build job that stays under 60 s bills 1 minute instead of 2.

**CI (`v4-ci.yml`).** Runs on code pushes to `v4-dev` / `main` and on pull requests. Documentation-only changes skip it.

## 3. Cost report before activation

Simulated on the **actual upcoming schedule**: real collector state, real stored games, the current AP poll, the real cron grid, and a 40-minute GitHub start delay. Seven days from 2026-10-29 02:00 UTC, scaled to a month (`tools_v4/consumption_check.py`).

### 3.1 Odds API credits

| | Expanded | Expanded, quiet backoff active | Balanced (approved) |
|---|---|---|---|
| **Credits/month** | **~3,570** | **~3,280** | ~756 |
| Peak calendar day | 186 | 183 | 51 |
| Worst rolling 24 h | 198 | 198 | — |
| Worst single hour | 15 | 15 | — |

**Snapshots per game in the final 72 h / 24 h / 6 h / 90 min (median):**

| | Expanded | Balanced |
|---|---|---|
| NFL | 68–71 / 34 / 18 / 6 | 17 / 10 / 5 / 2 |
| Top-25 CFB | 75–78 / 44 / 19 / 6 | 21 / 15 / 6 / 2 |

- Closes observed: every priority kickoff in the projection.
- The grid trimming above changed no snapshot count.
- A 3-week synthetic test with an international game also passes: every NFL and Top-25 close observed; 2,300–5,000 credits a month; rolling 24 h at most 250.
- **Limits (gateway, every request):** monthly cap 5,000 (code maximum 6,000); daily 250; per request 10; V3 floor 2,000. A fresh provider balance check (`/v4/sports`, 0 credits) runs before each paid request, and the request is refused if any check fails.
- The provider showed 19,940 remaining on Oct 29.

### 3.2 GitHub Actions minutes (private repository; GitHub Free includes 2,000/month)

Measured job durations: collect 7–14 s; dashboard build 24–83 s before the speed-up; analysis 22–24 s; CI 133–289 s.

| Workflow | Expanded | Balanced |
|---|---|---|
| odds-collect (238 runs/week × 4.35) | 1,035 | 766 |
| site-build (dashboard + Cloudflare deploy) | 374–413 builds → **374–516 min** (1 min each; 1.25 if some bill 2) | 209 → 209–261 |
| odds-analyze (daily) | 31 | 31 |
| cfb-rankings (2 runs/week + its rebuild) | 18 | 18 |
| v4-ci (≤ 45 code pushes × 3–4 min; docs-only pushes skip it) | 135–180 | 135–180 |
| odds-verify, manual rebuilds | 0 unless started by hand | 0 |
| **Total/month** | **~1,590–1,780; central estimate ~1,650** | ~1,160–1,260 |

**Caveats:**
- These are estimates. The real figure is on GitHub's usage page.
- GitHub often delays or drops scheduled runs: on Oct 27, about half the 15-minute runs never started. That lowers actual minutes, and the next run absorbs a missed close window.
- CI depends on how many code changes are pushed: 3–4 minutes each (the Oct 29 run took 3 min 32 s, billed as 4). Code changes are batched to stay at or under ~45 pushes a month.
- **Hard ceilings:**
  - collection can't exceed its 238 scheduled runs a week (~1,035 minutes);
  - collector dashboard builds stop at 440 a month.
  - The upper end of the range, 1,780, assumes no quiet backoff, a quarter of builds billing 2 minutes and a heavy CI month. If the first week's real usage trends above 1,750, the soft dashboard limit (380 builds) will be lowered, and that change reported.
- **The 2,000 included minutes are shared by every private repository on the account.** I can only see this repository.

### 3.3 Cloudflare Pages deployments (Free plan)

| | Per month |
|---|---|
| Collector-triggered deployments, projected | 374–413 |
| cfb-rankings deployments | ~9 |
| Hard self-limit on collector deployments | 440 (soft limit 380: after closes, otherwise every 6 h) |
| Maximum, including rankings and a few manual ones | < 500 |

**Free-plan limits:** from Cloudflare's documentation (`pages/platform/limits`, read from `cloudflare/cloudflare-docs`).

| Limit | Free plan | V4 |
|---|---|---|
| Builds per month | 500 | see the question below; designed to stay under 500 anyway |
| Concurrent builds | 1 | the `v4-site` concurrency group runs one at a time |
| Files per site | 20,000 | 3 (`index.html`, `_headers`, `robots.txt`) |
| Size per file | 25 MiB | `index.html` ≈ 0.8 MB |
| Preview deployments | unlimited | — |
| Pages Functions | none used | — |

**Does Direct Upload count against the 500 builds?** The documentation describes the 500-build limit as builds Cloudflare runs when you push to a connected Git repository. V4 builds on GitHub and uploads finished files, so Cloudflare runs no build. The documentation does **not** say explicitly whether direct-upload deployments count toward that 500. I could not verify it either way. The design assumes they **do** count and stays below 500 a month.

**Cloudflare Access (Zero Trust Free):**
- 50 seats; the owner uses 1.
- 2 of the 50 custom applications are used (main domain and `*.` previews).
- The deploy script still refuses to upload unless both addresses redirect to the Access login, before and after every upload.

**No paid Cloudflare feature is used.** On the Free plan, exceeding a limit blocks the action; there is nothing to bill.

## 4. Account settings the owner should verify manually

**This environment cannot see GitHub or Cloudflare account billing:** GitHub's billing API is blocked here, and the Cloudflare token can only deploy Pages. Please check these yourself. Never enable anything paid.

**GitHub** (your profile → Settings → Billing and licensing):
1. **Plan:** GitHub Free.
2. **Usage:** this month's Actions minutes, across all your private repositories.
3. **Budgets and alerts:** a GitHub Actions budget of **$0**, with **"Stop usage when budget limit is reached"** turned on. Add an email alert at 75% and 90% of the included minutes if offered.
   - With this set, running out of included minutes stops workflows until next month instead of charging.
   - Collection would halt, and the dashboard would show STALE.
4. **Payment information:** if no payment method is on file, GitHub cannot charge. If one is, the $0 budget with stop-usage is what prevents charges.
5. Leave Codespaces, Packages and Git LFS budgets at $0 too. V4 uses none of them; it stores data in release assets, which are not metered.

**Cloudflare:**
1. Account → Billing: **Free** plan, no subscriptions.
2. Zero Trust → Settings → Account: **Free** plan, 50 seats, seats used = 1.
3. Workers & Pages → `arbitredge-v4-staging` → Deployments: the count this month should track the deploy report.

## 5. Activation (owner only, after approving this report)

| Variable | Now | For expanded | Why |
|---|---|---|---|
| `V4_TIER` | balanced | `expanded` | activates the tier, its hourly runs and its tier-gated schedule |
| `V4_MONTHLY_CAP` | 1000 | **5000** | ~3,300–3,600 projected, about 40% headroom; the code maximum stays 6,000 |
| `V4_DAILY_CAP` | 60 | **250** | worst rolling 24 h projected at 198; 60 would refuse most game-day fetches |
| per-request cap | 10 | unchanged | a fetch costs 3 |

- Order: set `V4_DAILY_CAP` and `V4_MONTHLY_CAP` first, then `V4_TIER`.
- After the first week, `tools_v4/consumption_check.py --actual-since` compares the estimate with the real ledger. Report actual Actions minutes from GitHub's usage page against section 3.2.

**Provider limits.** At most 4 requests per run: 2 balance checks and 2 fetches. At most 15 credits in any hour.

## 6. Sharp-money and sportsbook integrity

**Sharp money.** Denser observations only feed the existing evidence rules:
- moves measured at market-making books;
- timing claims only when the observations are close enough;
- sport-specific thresholds;
- "INSUFFICIENT EVIDENCE" otherwise.

Modeled sportsbook splits are never used as evidence of real sharp action, and are never presented as market splits. Real splits remain UNAVAILABLE (no licensed source).

**Sportsbook engine m4 unchanged:**
- 10,000 + 10,000 persistent bettors;
- immutable `v4-book` wager files, replay-verified;
- Need kept separate from estimated value.

Mode: WATCH / paper; no official plays.
