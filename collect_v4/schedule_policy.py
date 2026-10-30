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

import time

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
    # EXPANDED (built 2026-10-29, NOT active until the owner approves; ~3,000-5,000 credits/month).
    # Kickoff-relative cadence plus calendar windows; cadence rules (never closes) back off x2 when the
    # sport's market barely moved since the last fetch and no priority kickoff is within 24 h.
    "expanded": {
        "rules": [(1.5, 0.25), (6, 0.5), (24, 1), (72, 2), (168, 4)],
        # (sport or None for both, UTC weekdays Mon=0, start_hour, end_hour, min_gap_h, needs kickoff within h)
        "windows": [
            ("americanfootball_nfl", (6,), 22, 24, 1, 216),        # NFL openers: Sunday night...
            ("americanfootball_nfl", (0,), 0, 8, 1, 216),          # ...into Monday morning (UTC)
            ("americanfootball_nfl", (0, 1), 12, 24, 2, 216),      # early-week moves Mon-Tue
            ("americanfootball_nfl", (2, 3, 4), 18, 24, 1, 120),   # injury-report windows Wed-Fri
            ("americanfootball_ncaaf", (6,), 17, 24, 1, 216),      # CFB openers Sunday afternoon/evening
            ("americanfootball_ncaaf", (0,), 0, 6, 1, 216),
            ("americanfootball_ncaaf", (0, 1), 12, 24, 2, 216),    # early-week moves Mon-Tue
        ],
        "quiet_change_rate": 0.03,
        "quiet_backoff": 2.0,
        # a run that is fetching one sport also fetches the other if its last fetch is >= 1 h old and it
        # has a priority kickoff within 7 days: more observations per GitHub Actions minute (the scarce
        # resource; credits are not)
        "piggyback": {"min_gap_h": 1.0, "within_h": 168},
    },
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


# GitHub starts scheduled runs 5-50 min late, by a different amount each time, so two runs one hour apart in
# the schedule can start 58 min apart. A gap counts as met within this tolerance (20% of the gap, at most
# 15 min); otherwise an hourly rule silently becomes every two hours (seen 2026-10-29 05:25Z).
GAP_TOLERANCE = 0.2
GAP_TOLERANCE_MAX_H = 0.25


def _gap_met(since_h, gap_h):
    return since_h >= gap_h - min(gap_h * GAP_TOLERANCE, GAP_TOLERANCE_MAX_H)


def due(now, kickoffs, last_fetch, tier="test", sport=None, change_rate=None):
    """Returns (is_due, reason). kickoffs: epoch seconds (any order); last_fetch: epoch or None.

    close: some kickoff k is <= CLOSE_WINDOW_MIN away and the last fetch was taken before
    k - CLOSE_WINDOW_MIN (so it does not already count as k's observed close). One fetch
    covers every kickoff in the next 45 minutes. Closes are never delayed by any backoff.
    change_rate: share of the sport's quotes that changed at the last fetch (None = unknown)."""
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
    quiet = (change_rate is not None and "quiet_change_rate" in t and change_rate < t["quiet_change_rate"]
             and to_next_h > 24)
    mult = t.get("quiet_backoff", 1.0) if quiet else 1.0
    for horizon_h, gap_h in t["rules"]:
        if to_next_h <= horizon_h and _gap_met(since_h, gap_h * mult):
            return True, f"cadence: next kickoff in {to_next_h:.1f} h, last fetch {since_h:.1f} h ago " \
                         f"(rule {horizon_h} h / {gap_h} h{' x%g quiet' % mult if quiet else ''})"
    g = time.gmtime(now)
    for w_sport, days, h0, h1, gap_h, need_h in t.get("windows", ()):
        if (w_sport in (None, sport) and g.tm_wday in days and h0 <= g.tm_hour < h1 and to_next_h <= need_h
                and _gap_met(since_h, gap_h * mult)):
            return True, f"window: {WEEKDAY[g.tm_wday]} {h0:02d}-{h1:02d} UTC every {gap_h} h"
    return False, "not due"


# Dashboard rebuilds (every tier). A rebuild is one GitHub Actions job (>= 1 billed minute) and one
# Cloudflare Pages deployment, so it happens only after a run that saved new odds, and then:
SITE_QUIET_GAP_S = 3 * 3600        # at most every 3 h in quiet periods
SITE_NEAR_GAP_S = 45 * 60          # every 45 min within SITE_NEAR_S of a priority (NFL / Top-25 CFB) kickoff
SITE_NEAR_S = 6 * 3600
# ... and always right after a closing-line fetch.
# Monthly self-limit (GitHub Free minutes, Cloudflare Free 500 builds/month, which may count direct uploads):
# Measured Oct 30: a build job takes 70-100 s on GitHub (cold or warm cache), so each bills 2 minutes; 300 builds
# = 600 minutes keeps the month's worst case (every scheduled collect run + heavy CI) under ~1,900 of the 2,000.
SITE_MONTH_SOFT = 280              # from here: only after closes, and at most every 6 h otherwise
SITE_MONTH_HARD = 300              # from here: no rebuilds until next month (the page shows its odds as stale)
SITE_SOFT_GAP_S = 6 * 3600


def site_due(now, last_site, priority_kickoffs, fetch_reasons, builds_this_month=0):
    """(rebuild?, reason) for a run that saved new odds (fetch_reasons: the reasons of its fetches)."""
    if not fetch_reasons:
        return False, "no new odds"
    if builds_this_month >= SITE_MONTH_HARD:
        return False, f"monthly dashboard limit reached ({builds_this_month} builds)"
    if any(r.startswith("close") for r in fetch_reasons):
        return True, "closing-line fetch"
    if builds_this_month >= SITE_MONTH_SOFT:
        if last_site is None or now - last_site >= SITE_SOFT_GAP_S:
            return True, f"monthly soft limit ({builds_this_month} builds): every 6 h"
        return False, f"monthly soft limit ({builds_this_month} builds): only after closes or every 6 h"
    near = any(0 < k - now <= SITE_NEAR_S for k in priority_kickoffs)
    gap = SITE_NEAR_GAP_S if near else SITE_QUIET_GAP_S
    if last_site is None or now - last_site >= gap:
        return True, ("final 6 h before a priority kickoff" if near else "quiet period") + f" (every {gap // 60} min)"
    return False, f"dashboard rebuilt {int((now - last_site) // 60)} min ago (every {gap // 60} min " + \
        ("in the final 6 h)" if near else "in quiet periods)")


WEEKDAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def piggyback(now, kickoffs, last_fetch, tier):
    """(True, reason) when another sport is being fetched in this run and this sport qualifies to ride
    along (tier setting 'piggyback'). Never used for tiers without it."""
    pb = TIERS.get(tier, {}).get("piggyback")
    if not pb or last_fetch is None:
        return False, "no piggyback"
    future = [k for k in kickoffs if k > now]
    if not future or min(future) - now > pb["within_h"] * 3600 or now - last_fetch < pb["min_gap_h"] * 3600:
        return False, "no piggyback"
    return True, f"piggyback: another sport is being fetched; last fetch {(now - last_fetch) / 3600:.1f} h ago"


def change_rate(prev, cur):
    """Share of quote keys whose (price, point) changed between two fetches' fingerprints.
    prev/cur: {key: fingerprint}. New and vanished keys count as changes. None if no previous."""
    if not prev:
        return None
    keys = set(prev) | set(cur)
    return sum(1 for k in keys if prev.get(k) != cur.get(k)) / len(keys) if keys else 0.0


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
