"""
sportsbook_v4/seal_run.py -- seal Need-tracker decisions at kickoff, independent of dashboard builds.

Runs as a no-secrets step of every odds-collect job:
  python -m sportsbook_v4.seal_run due --state out/collector_state.json      -> prints "yes"/"no" (no downloads)
  python -m sportsbook_v4.seal_run seal --state ... --need-dir need --out need_new
"due" is yes only when a priority game's kickoff passed between SEAL_WINDOW_S + grace and grace ago, so most runs
do nothing. "seal" freezes, for every such game that has pre-kickoff snapshots, the LAST snapshot taken and built
before kickoff (need_tracker.seal: the same rule the dashboard build uses, so either path produces the identical
decision; duplicates are tolerated only when identical). Games with no snapshot at all are left to the next
dashboard build, which knows the book's full game list and records them as NO_PRE_KICKOFF_SNAPSHOT.
"""

import argparse
import json
import os
import sys
import time

from sportsbook_v4 import need_tracker as NT

SEAL_WINDOW_S = 12 * 3600      # longer than any gap between collect runs (3-hourly base grid, plus GitHub delays)
SPORTS = {"americanfootball_nfl": "nfl", "americanfootball_ncaaf": "cfb"}


def candidates(state, now):
    out = []
    for key, sport in SPORTS.items():
        for e in (state.get(key) or {}).get("events") or []:
            gid, kick, home, away = e
            if now - NT.SEAL_GRACE_S - SEAL_WINDOW_S < kick <= now - NT.SEAL_GRACE_S:
                out.append({"sport": sport, "event_id": gid, "home": home, "away": away,
                            "kickoff": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(kick))})
    return out


def seal(state, need_dir, out_dir, now):
    games = candidates(state, now)
    snaps = NT.load(need_dir, "need_*.json.gz")
    decisions = NT.unique_decisions(NT.load(need_dir, "decisions_*.json.gz"))
    with_snap = {(s["sport"], s["event_id"]) for s in snaps}
    new = NT.seal([g for g in games if (g["sport"], g["event_id"]) in with_snap], snaps, decisions, now)
    path = NT.write(os.path.join(out_dir, "v4-need-decisions"), NT.decisions_name(now), new) if new else None
    return new, path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("due", "seal"))
    ap.add_argument("--state", required=True)
    ap.add_argument("--need-dir", default=None)
    ap.add_argument("--out", default="need_new")
    a = ap.parse_args(argv)
    now = time.time()
    state = json.load(open(a.state)) if os.path.exists(a.state) else {}
    if a.command == "due":
        print("yes" if candidates(state, now) else "no")
        return 0
    new, path = seal(state, a.need_dir, a.out, now)
    print(json.dumps({"sealed": [(d["sport"], d["away"], d["home"], d["status"]) for d in new],
                      "file": os.path.basename(path) if path else None}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
