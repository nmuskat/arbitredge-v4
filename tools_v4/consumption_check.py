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


def expected(state, snaps, tier, start, days, delay_s, known_events=None, change_rate=None):
    """change_rate: assumed share of quotes changing per fetch (None = unknown: no quiet backoff)."""
    sunday = calendar.timegm(time.gmtime(start)[:3] + (0, 0, 0)) - ((time.gmtime(start).tm_wday + 1) % 7) * 86400
    runs = sorted(t + delay_s for w in range(days // 7 + 2) for t in cron_plan.run_times(sunday + w * 7 * 86400, tier)
                  if start <= t + delay_s < start + days * 86400)
    out = []
    sports = {sp: {"events": st.get("events"), "last": st.get("odds_at"), "kick": st.get("kickoffs", [])}
              for sp, st in state.items() if isinstance(st, dict)}       # skips bookkeeping such as _site_at
    for t in runs:
        dec = {}
        for sport, v in sports.items():
            ks = (SP.priority_kickoffs(sport, v["events"], snaps, t)[0] if v["events"] is not None
                  else [k for k in v["kick"] if k > t])
            dec[sport] = (ks, *SP.due(t, ks, v["last"], tier, sport=sport, change_rate=change_rate))
        if any(d[1] for d in dec.values()):
            for sport, (ks, ok, why) in list(dec.items()):
                if not ok:
                    ride, r = SP.piggyback(t, ks, sports[sport]["last"], tier)
                    if ride:
                        dec[sport] = (ks, True, r)
        upcoming = sorted(k for d in dec.values() for k in d[0] if k > t)[:1]
        for sport, (_ks, ok, why) in dec.items():
            if ok:
                out.append({"sport": sport, "at": _iso(t), "reason": why, "credits": CREDITS,
                            "next_priority_kick": upcoming[0] if upcoming else None})
                v = sports[sport]
                v["last"] = t
                if v["events"] is None and known_events and sport in known_events:
                    v["events"] = known_events[sport]        # the fetch stores the games (new state format)
    return sorted(out, key=lambda r: r["at"])


def snapshots_per_game(exp, state, snaps, known_events, start, days):
    """For every priority game kicking off inside the projection: fetches of its sport in the final
    72 h / 24 h / 6 h / 90 min, and whether its close (a fetch <= 45 min before) is observed."""
    out = {}
    for sport, st in state.items():
        if not isinstance(st, dict):
            continue
        events = st.get("events") or (known_events or {}).get(sport) or []
        ks = SP.priority_kickoffs(sport, events, snaps, start)[0]
        fts = [calendar.timegm(time.strptime(f["at"], "%Y-%m-%dT%H:%M:%SZ")) for f in exp if f["sport"] == sport]
        rows = []
        for k in sorted(set(ks)):
            if not (start + 72 * 3600 <= k < start + days * 86400):    # full 72 h visible inside the projection
                continue
            rows.append({w: sum(1 for f in fts if 0 < k - f <= w * 3600) for w in (72, 24, 6, 1.5)}
                        | {"close": any(0 < k - f <= SP.CLOSE_WINDOW_MIN * 60 for f in fts)})
        if rows:
            out[sport] = {"kickoff_times": len(rows), "closes_observed": sum(r["close"] for r in rows),
                          **{f"median_final_{w}h": sorted(r[w] for r in rows)[len(rows) // 2] for w in (72, 24, 6, 1.5)},
                          **{f"min_final_{w}h": min(r[w] for r in rows) for w in (72, 24, 6, 1.5)}}
    return out


CI_RUNS_PER_MONTH = 50       # code pushes (documentation-only pushes skip CI); measured 3 billed minutes each
CI_MIN = 3
SITE_MIN = 1.25              # a dashboard build bills 1 minute, occasionally 2 late in a busy week


def site_builds(exp):
    """Dashboard rebuilds the collector would trigger (schedule_policy.site_due) for these fetches."""
    by_run = collections.OrderedDict()
    for f in exp:
        by_run.setdefault(f["at"], []).append(f)
    builds, last = [], None
    for at, fs in by_run.items():
        t = calendar.timegm(time.strptime(at, "%Y-%m-%dT%H:%M:%SZ"))
        kicks = [f["next_priority_kick"] for f in fs if f.get("next_priority_kick")]
        ok, _why = SP.site_due(t, last, kicks, [f["reason"] for f in fs])      # unthrottled: the monthly limit
                                                                                # only matters if this exceeds it
        if ok:
            builds.append(at)
            last = t
    return builds


def actions_minutes(tier, exp, start, days):
    """Billed GitHub Actions minutes per month, every workflow (each job rounds up to whole minutes)."""
    wk = calendar.timegm(time.gmtime(start)[:3] + (0, 0, 0))
    runs_week = len(cron_plan.run_times(wk, tier))
    scale = 4.35 / (days / 7)
    builds = round(len(site_builds(exp)) * scale)
    m = {"collect": round(runs_week * 4.35),
         "site_builds": builds, "site_build_minutes": round(builds * SITE_MIN),
         "analysis": 31,                          # odds-analyze: daily, 1 minute
         "rankings": 9 + 9,                       # cfb-rankings: 2 scheduled runs a week + its dashboard rebuild
         "ci": CI_RUNS_PER_MONTH * CI_MIN}
    m["total"] = m["collect"] + m["site_build_minutes"] + m["analysis"] + m["rankings"] + m["ci"]
    m["cloudflare_deployments"] = builds + 9     # one per dashboard build (collector + rankings)
    return m


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
    ap.add_argument("--change-rate", type=float, default=None,
                    help="assumed share of quotes changing per fetch (enables the expanded quiet backoff)")
    a = ap.parse_args(argv)
    start = calendar.timegm(time.strptime(a.start, "%Y-%m-%dT%H:%M:%SZ")) if a.start else time.time()
    state = json.load(open(os.path.join(a.state, "collector_state.json")))
    snaps = load_rankings(a.rankings_dir) if a.rankings_dir else []
    exp = expected(state, snaps, a.tier, start, a.days, a.delay_min * 60,
                   events_from_shards(a.shards) if a.shards else None, a.change_rate)
    known = events_from_shards(a.shards) if a.shards else None
    per_day = collections.Counter(r["at"][:10] for r in exp)
    report = {"tier": a.tier, "from": _iso(start), "days": a.days,
              "expected_credits": CREDITS * len(exp), "expected_per_day": {d: CREDITS * n for d, n in sorted(per_day.items())},
              "expected_per_month": round(CREDITS * len(exp) * 30.4 / a.days),
              "snapshots_per_game": snapshots_per_game(exp, state, snaps, known, start, a.days),
              "actions_minutes_per_month": actions_minutes(a.tier, exp, start, a.days),
              "expected_fetches": exp}
    if a.actual_since:
        report["actual_since"] = a.actual_since
        report["actual"] = actual(os.path.join(a.state, "ledger.jsonl"),
                                  calendar.timegm(time.strptime(a.actual_since, "%Y-%m-%dT%H:%M:%SZ")))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
