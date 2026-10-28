"""
tools_v4/collection_plan.py -- PLANNING ONLY: credits and close coverage of candidate collection tiers.

Nothing here changes what the live collector does (collect_v4.schedule_policy decides that, and
the tier is the owner's repository variable V4_TIER). This simulates candidate policies on the
real GitHub Actions cron grid (collect_v4.cron_plan) with real kickoff times, GitHub's late
starts and dropped runs, and reports per month:
  credits   (one fetch = one sport = 3 credits: spreads+totals+h2h x 1 region, ALL games of the sport)
  closes    share of kickoff times with an observed close (a fetch <= 45 min before kickoff)
  snapshots in the final 72 h before each kickoff

A policy is: close fetches before each priority kickoff, plus "cadence" rules
(horizon_h, min_gap_h): fetch when the next priority kickoff is within horizon_h and the last
fetch is at least min_gap_h old. For CFB the priority kickoffs can be all FBS or AP Top-25 only
(each fetch still returns every FBS game; only WHEN to fetch changes).

  python3 tools_v4/collection_plan.py --shards <dir> --rankings-dir <dir>
"""

import argparse
import calendar
import glob
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collect_v4 import cron_plan  # noqa: E402

CLOSE_MIN = 45
CREDITS_PER_FETCH = 3
WEEKS_PER_MONTH = 4.35

# (horizon_h, min_gap_h) cadence rules; closes are always on
POLICIES = {
    "test (current)": [(24, 12), (72, 24)],                     # = schedule_policy "test"
    "balanced": [(6, 2), (24, 4), (72, 8), (168, 24)],
    "aggressive": [(3, 0.75), (12, 1.5), (24, 3), (72, 6), (168, 12)],
}


def due(now, kicks, last, rules):
    future = [k for k in kicks if k > now]
    if last is None:
        return True
    if not future:
        return now - last >= 24 * 3600
    w = CLOSE_MIN * 60
    if any(k - now <= w and last < k - w for k in future):
        return True
    to_next = min(future) - now
    return any(to_next <= h * 3600 and now - last >= g * 3600 for h, g in rules)


def simulate(kicks, rules, week0, drop=0.05, delay=(25, 50), seed=11):
    """Three consecutive weeks of the same kickoff pattern; score the middle week."""
    rng = random.Random(seed)
    allk = sorted({k + w * 7 * 86400 for w in range(3) for k in kicks})
    runs = [t for w in range(3) for t in cron_plan.run_times(week0 + w * 7 * 86400)]
    last, fetches = None, []
    for t in runs:
        if rng.random() < drop:
            continue
        t2 = t + rng.randint(delay[0] * 60, delay[1] * 60)
        if due(t2, allk, last, rules):
            fetches.append(t2)
            last = t2
    lo, hi = week0 + 7 * 86400, week0 + 14 * 86400
    mid = [k for k in allk if lo <= k < hi]
    closed = sum(1 for k in mid if any(0 < k - f <= CLOSE_MIN * 60 for f in fetches))
    snaps72 = [sum(1 for f in fetches if 0 < k - f <= 72 * 3600) for k in mid]
    n = sum(1 for f in fetches if lo <= f < hi)
    return {"fetches_per_week": n, "credits_per_month": round(n * CREDITS_PER_FETCH * WEEKS_PER_MONTH),
            "kickoff_times": len(mid), "closes_observed": closed,
            "median_snapshots_last_72h": sorted(snaps72)[len(snaps72) // 2] if snaps72 else 0}


def kickoffs(shards_dir, rankings_dir):
    from analysis_v4.actions_analyze import rebuild
    from data_v4 import rankings
    from sportsbook_v4 import live_book as LB
    from web_v4.live_site import load_rankings
    live, _ = rebuild(sorted(glob.glob(os.path.join(shards_dir, "**", "quotes_*.jsonl.gz"), recursive=True)))
    snaps = load_rankings(rankings_dir)
    ws = calendar.timegm((2026, 10, 27, 12, 0, 0))
    out = {}
    for sport in ("nfl", "cfb"):
        games = LB.week_games(live, sport, ws, ws + 7 * 86400, 10 ** 10)
        out[sport] = [g["kickoff"] for g in games]
        if sport == "cfb":
            out["cfb_top25"] = [g["kickoff"] for g in games
                                if (lambda s: s["status"] == "OK" and s["top25"])(
                                    rankings.top25_status(g["home"], g["away"], g["kickoff"], snaps))]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", required=True)
    ap.add_argument("--rankings-dir", required=True)
    a = ap.parse_args(argv)
    k = kickoffs(a.shards, a.rankings_dir)
    week0 = calendar.timegm((2026, 10, 18, 0, 0, 0))          # Sunday; the real week is the middle one
    shift = 7 * 86400
    k = {s: [t - shift for t in v] for s, v in k.items()}     # template week -> first simulated week
    report = {"kickoffs": {s: len(set(v)) for s, v in k.items()}, "tiers": {}}
    for name, rules in POLICIES.items():
        nfl = simulate(k["nfl"], rules, week0)
        for scope in ("cfb", "cfb_top25"):
            cfb = simulate(k[scope], rules, week0)
            report["tiers"][f"{name} | CFB {'all FBS' if scope == 'cfb' else 'Top-25'}"] = {
                "nfl": nfl, "cfb": cfb, "credits_per_month": nfl["credits_per_month"] + cfb["credits_per_month"]}
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
