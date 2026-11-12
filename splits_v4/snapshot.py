"""
splits_v4/snapshot.py -- take ONE splits snapshot for a slate (run by .github/workflows/splits-collect.yml, no secrets).

  python -m splits_v4.snapshot --state collector_state.json --slate EPOCH --timing "ON TIME" --out DIR

Writes DIR/splits_<UTC time>.json: the slate's games (from the collector's free event list), every site's status,
and each site's numbers for each matched game. Written once, never modified (release v4-splits).
"""
import argparse
import json
import os
import time

from splits_v4 import match as M
from splits_v4 import slates, sources

SPORTS = {"americanfootball_nfl": "nfl", "americanfootball_ncaaf": "cfb"}


def slate_events(state, slate):
    evs = []
    for key, sp in SPORTS.items():
        for eid, ko, home, away in slates.members((state.get(key) or {}).get("events", []), slate):
            evs.append({"event_id": eid, "sport": sp, "kickoff": ko, "home": home, "away": away})
    return evs


def take(state, slate, timing, now, get=sources.fetch):
    events = slate_events(state, slate)
    year = time.gmtime(now).tm_year
    sports = sorted({e["sport"] for e in events}) or ["nfl", "cfb"]
    raw = sources.collect(year, get=get, sports=tuple(sports))
    games, status = {e["event_id"]: {} for e in events}, {}
    rot = {}
    sbd, un = M.match_rows(raw.get("sportsbettingdime", {}).get("rows", []), events)
    for eid, r in sbd.items():
        if r.get("rot"):
            rot[tuple(r["rot"])] = eid
    for name, res in raw.items():
        got, un = (sbd, un) if name == "sportsbettingdime" else M.match_rows(res["rows"], events, rot)
        for eid, r in got.items():
            games[eid][name] = {k: v for k, v in r.items() if k not in ("source",)}
        status[name] = {"status": res["status"], "rows": len(res["rows"]), "matched": len(got), "unmatched": un}
    return {"version": "splits_snapshot.v1", "taken_at": now, "taken_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "slate": slate, "slate_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(slate)), "timing": timing,
            "events": events, "sources": status, "games": games}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--slate", type=float, required=True)
    ap.add_argument("--timing", default="ON TIME")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    now = time.time()
    doc = take(json.load(open(a.state)), a.slate, a.timing, now)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"splits_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))}.json")
    with open(path, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"), sort_keys=True)
    print(f"splits snapshot: slate {doc['slate_iso']} ({a.timing}), {len(doc['events'])} games; " +
          "; ".join(f"{k} {v['status'][:20]} matched {v['matched']}/{v['rows']}" for k, v in doc["sources"].items()))


if __name__ == "__main__":
    main()
