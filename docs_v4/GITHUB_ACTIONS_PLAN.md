# Live odds collection on GitHub Actions: design for owner approval

Status: **built and tested, NOT active.** Nothing runs and no API request is made until the owner adds the API key and sets the `V4_LIVE` switch (steps at the end).

## Feasibility answers (from GitHub's documentation, read 2026-10-26)

1. **Store the key securely.** **Yes.**
   - It is a repository secret `ODDS_API_KEY`, encrypted, and redacted from logs (redaction is best-effort, so our code also never prints it).
   - It is injected only into the collect step and the manual verify step.
   - Not available on GitHub Free for private repositories: environment-level secrets with approval reviewers.
2. **Run live requests without your Mac.** **Yes.** GitHub-hosted Ubuntu runners. Each run lasts under a minute and needs no extra software (Python's standard library only).
3. **Enforce 500 credits and protect V3.** **Yes**, in our code, before every request:
   - V4 monthly cap: variable `V4_MONTHLY_CAP`, default **500**, hard-limited to **6,000**.
   - 24-hour cap: default 60.
   - Per-request ceiling: 10, never more than 30.
   - **V3 floor:** refuse if the provider's own `x-requests-remaining` would drop below 2,000. The floor can be raised, never lowered.

   The credit ledger persists between runs. If the stored ledger exists but can't be downloaded, the run **fails instead of starting from zero**.
4. **Timestamped multi-book odds.** **Yes.**
   - One `/odds` request returns every NFL (or CFB) game for 10 books × spreads/totals/moneyline, costing 3 credits.
   - Books: Pinnacle, BetOnline, LowVig (market makers) + DraftKings, FanDuel, BetMGM, Caesars, BetRivers, ESPN BET, Fanatics.
   - Every row keeps the book's update time and our fetch time.
5. **Persist data without Git.** **Yes, as release assets** (not Git history, and not counted against Actions storage):
   - Each run writes one compressed file to a weekly release `odds-data-YYYY-Www`. Limits: 1,000 files per release, each under 2 GiB, no total-size limit.
   - The ledger and collector state live in release `v4-state`.
   - Not used: the Actions cache (evicted after 7 days unused) and artifacts (expire, and count against 500 MB).
6. **Scheduled paper tracking and analysis.** **Yes.**
   - Daily job (`odds-analyze`, 13:23 UTC) rebuilds the full history and runs the line-shopping **closing-EV** test.
   - Results go to the run page and to `v4-state/line_shop_latest.json`.
   - It uses no API credits and no secret.
7. **Stay within free limits.** **Yes**, with about 40% of the quota used:
   - **Collection:** about 162 short runs a week, roughly 705 minutes a month. Most runs only check whether a fetch is due.
   - **Analysis:** about 120 minutes a month.
   - **CI:** about 120 minutes a month.
   - **Total:** about 950 of the **2,000** free minutes.
   - Without a payment method on file, GitHub **blocks** usage at the quota and **cannot bill**. If you have a payment method, set an Actions budget of $0 (Settings → Billing → Budgets).
   - While `V4_LIVE` is off, the scheduled jobs are *skipped* and use 0 minutes.

## Delays and dropped runs

GitHub warns that scheduled runs can be delayed at busy times, especially on the hour, and occasionally dropped. The design absorbs this:
- **Off-peak times:** runs are scheduled at :02/:17/:32/:47, never :00.
- **Every run decides for itself whether a fetch is due**, using the last fetch time and known kickoffs. A late or missing run is covered by the next one.
- **Closing lines:** a fetch is due when a kickoff is ≤ 45 minutes away and not already covered. Every 15 minutes during game windows gives about 3 chances per kickoff.
- **Never invented:** if a close is still missed, it is reported as "not observed" and excluded.

Simulated week (tested in `tests_collect/test_actions.py`):

| Scenario | Credits / month (test tier) | Closing lines observed |
|---|---|---|
| No disruption | about 300 | **all** NFL and CFB kickoff slots |
| 25% of runs dropped, up to 40 min late | at most 500 | at least 75% |

At the full 6,000 budget ("standard" tier, more intraday snapshots), the simulation shows about 600–950 credits/month, so there is room to add more snapshots later.

Not covered: college games kicking off on weekday nights (mostly non-Top-25). Adding those windows costs about 100 Actions minutes a month.

## Security model and limits

- **Key exposure:** the key is never in Git or in any stored file (tested), and the code never prints it. GitHub's redaction is a second layer.
- **Third-party code:** only GitHub's own `actions/checkout`, pinned to an exact commit.
- **Write access:** jobs get `contents: write` only to store release assets. Checkout does not keep the token.
- **Overlapping runs:** collection, verification and analysis share one concurrency group, so the ledger can never be written by two runs at once.
- **Honest limit:** on GitHub Free private repositories there are no branch protection or required-reviewer rules. Anyone with write access to the repository (you, and Claude through the GitHub App) could in principle change a workflow to misuse the secret. The guardrails are the rules in `CLAUDE.md`, the fully visible history of every workflow change, and your Odds API dashboard as an independent check on usage.
- **Failure accounting:** if a run fails after a request but before saving the ledger, at most one request (3 credits) goes unrecorded. The provider-reported floor still protects V3.

## Cheapest alternative, if Actions is ever unsuitable

A free-tier cloud VM (Oracle Cloud Always Free or Google Cloud e2-micro) would run 24/7. Both require a credit card on file and add setup and maintenance. GitHub Actions needs neither, so it is the recommended path.

## Owner activation steps (only after you approve)

1. **Add the secret:** GitHub repository → Settings → Secrets and variables → Actions → **New repository secret**. Name it `ODDS_API_KEY` and paste the key. GitHub hides it from then on.
2. **Run the free verification:** Actions tab → **odds-verify** → **Run workflow**. This makes one `/v4/sports` request (0 credits), confirms the key works, and shows the remaining credits.
3. **Turn on collection:** Settings → Secrets and variables → Actions → **Variables** tab → New variable `V4_LIVE` = `on`. Optionally add `V4_MONTHLY_CAP` = `500` to make it explicit.
4. **To stop at any time:** change `V4_LIVE` to `off`.
