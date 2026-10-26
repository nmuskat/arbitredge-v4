"""
collect_v4/schedule_policy.py -- WHEN to spend credits, given a budget tier.

GitHub Actions cannot poll every few minutes for free, and scheduled runs
can be delayed or dropped, so the policy is "opportunistic": every run asks
"is a fetch due now?" and several runs get a chance at each important
moment (multiple attempts inside each window absorb delays and drops).

Per sport, a fetch is due when (TEST tier values):
  close   a kickoff is <= 45 min away and not already covered by the last fetch
          (gives an OBSERVED closing line for that kickoff);
  near    the next kickoff is within 24 h and the last fetch is >= 12 h old;
  daily   the next kickoff is within 3 days and the last fetch is >= 24 h old;
  bootstrap   no fetch yet.
Credit and minute costs are simulated in tests_collect (a full NFL + CFB week,
with dropped and delayed runs) so the budget is checked before anything runs.
"""

CLOSE_WINDOW_MIN = 45        # = the freshness rule: a quote <= 45 min before kickoff is an OBSERVED close
TIERS = {
    # initial 500 credits/month: closes + one daily snapshot within 3 days + one same-day snapshot
    "test": {"near_h": 24, "near_gap_h": 12, "daily_days": 3, "daily_gap_h": 24},
    # up to the approved 6,000/month: more intraday snapshots for line-shopping detection
    "standard": {"near_h": 48, "near_gap_h": 3, "daily_days": 7, "daily_gap_h": 12},
}


def due(now, kickoffs, last_fetch, tier="test"):
    """Returns (is_due, reason). kickoffs: epoch seconds (any order); last_fetch: epoch or None.

    close: some kickoff k is <= CLOSE_WINDOW_MIN away and the last fetch was taken before
    k - CLOSE_WINDOW_MIN (so it does not already count as k's observed close). One fetch
    covers every kickoff in the next 45 minutes."""
    t = TIERS[tier]
    future = sorted(k for k in kickoffs if k > now)
    if last_fetch is None:
        return True, "bootstrap"
    since_min = (now - last_fetch) / 60.0
    if not future:
        return (since_min >= 24 * 60, "no known upcoming games: daily discovery" if since_min >= 24 * 60 else "idle")
    w = CLOSE_WINDOW_MIN * 60
    pending = [k for k in future if k - now <= w and last_fetch < k - w]
    if pending:
        return True, f"close snapshot ({(pending[0] - now) / 60:.0f} min to kickoff)"
    to_next = (future[0] - now) / 60.0
    if to_next <= t["near_h"] * 60 and since_min >= t["near_gap_h"] * 60:
        return True, "pre-game snapshot"
    if to_next <= t["daily_days"] * 1440 and since_min >= t["daily_gap_h"] * 60:
        return True, "daily snapshot"
    return False, "not due"
