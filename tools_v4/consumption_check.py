"""
tools_v4/consumption_check.py -- expected vs actual Odds API consumption (no API calls).

Expected: replays collect_v4.schedule_policy (the collector's own rules) over the real cron grid
for the coming days, starting from the real collector_state.json (last fetch + known games per
sport) and the AP-poll snapshots, with a typical GitHub start delay. Actual: the credit ledger.

  python3 tools_v4/consumption_check.py --state <dir with collector_state.json, ledger.jsonl>
      --rankings-dir <dir> --tier balanced [--from ISO] [--days 7] [--delay-min 40]
"""

import argparse
import calendar
import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collect_v4 import cron_plan, schedule_policy as SP  # noqa: E402
from collect_v4.actions_run import load_rankings  # noqa: E402

CREDITS = 3


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def events_from_shards(shards_dir):
    """{sport: [[event_id, kickoff, home, away], ...]} from collected shards: what the collector stores
    in its state after its next fetch of that sport."""
    import glob
    from analysis_v4.actions_analyze import rebuild
    conn, _ = rebuild(sorted(glob.glob(os.path.join(shards_dir, "**", "quotes_*.jsonl.gz"), recursive=True)))
    out = {}
    for sport, e, k, h, a in conn.execute("SELECT sport, event_id, MIN(commence), home, away FROM mkt_quote "
                                          "GROUP BY event_id ORDER BY 3"):
        out.setdefault(sport, []).append([e, k, h, a])
    return out


def expected(state, snaps, tier, start, days, delay_s, known_events=None):
    sunday = calendar.timegm(time.gmtime(start)[:3] + (0, 0, 0)) - ((time.gmtime(start).tm_wday + 1) % 7) * 86400
    runs = sorted(t + delay_s for w in range(days // 7 + 2) for t in cron_plan.run_times(sunday + w * 7 * 86400)
                  if start <= t + delay_s < start + days * 86400)
    out = []
    for sport, st in state.items():
        events, last = st.get("events"), st.get("odds_at")
        for t in runs:
            ks = (SP.priority_kickoffs(sport, events, snaps, t)[0] if events is not None
                  else [k for k in st.get("kickoffs", []) if k > t])
            ok, why = SP.due(t, ks, last, tier)
            if ok:
                out.append({"sport": sport, "at": _iso(t), "reason": why, "credits": CREDITS})
                last = t
                if events is None and known_events and sport in known_events:
                    events = known_events[sport]        # the fetch stores the games (new state format)
    return sorted(out, key=lambda r: r["at"])


def actual(ledger_path, since):
    rows = [json.loads(x) for x in open(ledger_path) if x.strip()] if os.path.exists(ledger_path) else []
    used = [r for r in rows if r["ts"] >= since and r.get("endpoint") == "odds"]
    return {"requests": len(used), "credits": sum((r["actual_cost"] if r["actual_cost"] is not None else r["est_cost"])
                                                  for r in used),
            "provider_remaining": rows[-1].get("hdr_remaining") if rows else None}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--rankings-dir", default=None)
    ap.add_argument("--tier", default="balanced")
    ap.add_argument("--from", dest="start", default=None)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--delay-min", type=int, default=40)
    ap.add_argument("--actual-since", default=None, help="ISO time to count actual ledger spend from")
    ap.add_argument("--shards", default=None, help="collected shards: games the next fetch will store")
    a = ap.parse_args(argv)
    start = calendar.timegm(time.strptime(a.start, "%Y-%m-%dT%H:%M:%SZ")) if a.start else time.time()
    state = json.load(open(os.path.join(a.state, "collector_state.json")))
    snaps = load_rankings(a.rankings_dir) if a.rankings_dir else []
    exp = expected(state, snaps, a.tier, start, a.days, a.delay_min * 60,
                   events_from_shards(a.shards) if a.shards else None)
    per_day = collections.Counter(r["at"][:10] for r in exp)
    report = {"tier": a.tier, "from": _iso(start), "days": a.days,
              "expected_credits": CREDITS * len(exp), "expected_per_day": {d: CREDITS * n for d, n in sorted(per_day.items())},
              "expected_fetches": exp}
    if a.actual_since:
        report["actual_since"] = a.actual_since
        report["actual"] = actual(os.path.join(a.state, "ledger.jsonl"),
                                  calendar.timegm(time.strptime(a.actual_since, "%Y-%m-%dT%H:%M:%SZ")))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
