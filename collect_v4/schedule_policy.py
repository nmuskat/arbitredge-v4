"""
collect_v4/schedule_policy.py -- WHEN to spend credits, given a budget tier.

GitHub Actions cannot poll every few minutes for free, and scheduled runs
can be delayed or dropped, so the policy is "opportunistic": every run asks
"is a fetch due now?" and several runs get a chance at each important
moment (multiple attempts inside each window absorb delays and drops).

Per sport, a fetch is due when:
  close   a PRIORITY kickoff is <= 45 min away and not already covered by the last fetch
          (gives an OBSERVED closing line for that kickoff);
  cadence for some rule (horizon_h, min_gap_h) of the tier: the next priority kickoff is within
          horizon_h and the last fetch is at least min_gap_h old;
  bootstrap   no fetch yet;  discovery   no known priority kickoff and the last fetch >= 24 h old.
PRIORITY kickoffs: every NFL game; for CFB, games involving an AP Top-25 team under the poll that
applies at kickoff (games whose ranking cannot be determined also count, so a missing poll never
silently drops a Top-25 close -- the credit gate still bounds spend). One fetch still returns every
game of the sport; only WHEN to fetch is targeted.
Credit and minute costs are simulated in tests_collect and tools_v4/collection_plan.py (real cron
grid, delayed and dropped runs) before anything runs.
"""

CLOSE_WINDOW_MIN = 45        # = the freshness rule: a quote <= 45 min before kickoff is an OBSERVED close
TIERS = {
    # initial 500 credits/month: closes + one daily snapshot within 3 days + one same-day snapshot
    "test": {"rules": [(24, 12), (72, 24)]},
    # owner-approved 2026-10-29 (~757 credits/month with Top-25 CFB closes; ~15 snapshots in the final 72 h)
    "balanced": {"rules": [(6, 2), (24, 4), (72, 8), (168, 24)]},
    # planning only (~979/month)
    "aggressive": {"rules": [(3, 0.75), (12, 1.5), (24, 3), (72, 6), (168, 12)]},
    # legacy upper bound kept for the budget tests
    "standard": {"rules": [(48, 3), (168, 12)]},
}


DEFAULT_TIER = "test"        # the LOWEST-spending tier; never a fallback to anything higher


class InvalidTier(ValueError):
    pass


def resolve_tier(value):
    """Turn the V4_TIER setting into a tier name.

    GitHub Actions passes an UNDEFINED repository variable as an empty string, so
    unset/empty/whitespace all mean "not configured" -> DEFAULT_TIER ("test").
    Any other value must name a known tier exactly (case/space-insensitive);
    otherwise InvalidTier is raised so the run stops before any request."""
    if value is None or not str(value).strip():
        return DEFAULT_TIER, "default (V4_TIER not set)"
    name = str(value).strip().lower()
    if name not in TIERS:
        raise InvalidTier(f"V4_TIER={value!r} is not a known tier {sorted(TIERS)}; refusing to collect")
    return name, "V4_TIER"


def due(now, kickoffs, last_fetch, tier="test"):
    """Returns (is_due, reason). kickoffs: epoch seconds (any order); last_fetch: epoch or None.

    close: some kickoff k is <= CLOSE_WINDOW_MIN away and the last fetch was taken before
    k - CLOSE_WINDOW_MIN (so it does not already count as k's observed close). One fetch
    covers every kickoff in the next 45 minutes."""
    if tier not in TIERS:
        raise InvalidTier(f"unknown tier {tier!r}")
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
    to_next_h = (future[0] - now) / 3600.0
    since_h = since_min / 60.0
    for horizon_h, gap_h in t["rules"]:
        if to_next_h <= horizon_h and since_h >= gap_h:
            return True, f"cadence: next kickoff in {to_next_h:.1f} h, last fetch {since_h:.1f} h ago " \
                         f"(rule {horizon_h} h / {gap_h} h)"
    return False, "not due"


def priority_kickoffs(sport, events, rankings_snapshots, now):
    """Kickoffs that drive collection timing. events: [[event_id, kickoff, home, away], ...].
    NFL: all. CFB: AP Top-25 games under the poll applicable at kickoff, plus games whose status
    cannot be determined (never silently dropped). Returns (kickoffs, {"top25": n, "unknown": n, "unranked": n})."""
    future = [e for e in events if e[1] > now]
    if sport != "americanfootball_ncaaf":
        return sorted(e[1] for e in future), {"all": len(future)}
    from data_v4 import rankings
    snaps = [s for s in rankings_snapshots or [] if s["fetched_at"] <= now]
    keep, n = [], {"top25": 0, "unknown": 0, "unranked": 0}
    for _eid, k, home, away in future:
        st = rankings.top25_status(home, away, k, snaps) if snaps else {"status": "UNAVAILABLE"}
        if st["status"] == "OK" and not st["top25"]:
            n["unranked"] += 1
            continue
        n["top25" if st["status"] == "OK" else "unknown"] += 1
        keep.append(k)
    return sorted(keep), n
