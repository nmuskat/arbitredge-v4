"""
research_v4/panel.py -- build the normalized price panel (RESEARCH ONLY, development data).

One row per (event, market, fetch, book): point, prices, implied fair line mu (A1), plus event metadata
(sport, kickoff, final). Inputs: quote shards (live odds-data-* and historical odds-hist), final-score files.

  python -m research_v4.panel --shards DIR [DIR ...] --results DIR --out panel.json.gz
"""

import argparse
import collections
import glob
import gzip
import json
import os
import statistics as st

import numpy as np

from data_v4 import results as R
from research_v4 import normalize as NZ

SPORT = {"americanfootball_nfl": "nfl", "americanfootball_ncaaf": "cfb"}
SIDES = {"spreads": ("home", "away"), "totals": ("over", "under")}
MAKERS = ("pinnacle", "betonlineag", "lowvig")      # NOTE: betonlineag and lowvig are the same operator group
REC = ("draftkings", "fanduel", "betmgm", "williamhill_us", "betrivers", "espnbet", "fanatics")
OWNER = ("fanatics", "betonlineag", "lowvig")


def load_quotes(dirs):
    q = collections.defaultdict(dict)     # (event, market, fetched_at, book) -> {side: (point, price)}
    meta = {}
    for d in dirs:
        for p in sorted(glob.glob(os.path.join(d, "**", "*quotes_*.jsonl.gz"), recursive=True)):
            for line in gzip.open(p, "rt"):
                r = json.loads(line)
                if r["t"] != "mkt_quote" or r["market"] not in SIDES:
                    continue
                q[(r["event_id"], r["market"], r["fetched_at"], r["book"])][r["side"]] = (r["point"], r["price"])
                meta[r["event_id"]] = (SPORT.get(r["sport"]), r["home"], r["away"], r["commence"])
    return q, meta


def finals(results_dir, meta):
    """{event_id: (home, away)} via the platform's own matcher (NFL by names+date; CFB by school matcher)."""
    from data_v4 import rankings
    docs = R.load(results_dir)
    snaps = sorted(glob.glob(os.path.join(os.path.dirname(results_dir.rstrip("/")), "..", "build", "rankings", "*.json")))
    matcher = None
    games = [{"sport": s, "event_id": e, "home": h, "away": a,
              "kickoff": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime(k))}
             for e, (s, h, a, k) in meta.items() if s]
    teams = []
    for p in snaps:
        try:
            teams = json.load(open(p)).get("teams") or teams
        except (OSError, ValueError):
            pass
    if teams:
        matcher = rankings.TeamMatcher(teams)
    res = R.match(games, docs, matcher)
    return {e: (v["home_score"], v["away_score"]) for (s, e), v in res.items()}


def build(dirs, results_dir):
    q, meta = load_quotes(dirs)
    fin = finals(results_dir, meta)
    nfl_m, nfl_t = NZ.nfl()
    # CFB normalizers: key weights from CFB final scores in the results file; sigma from development residuals
    docs = R.load(results_dir)
    cfb_rows = [r for d in docs if d["sport"] == "cfb" for r in d["rows"] if r.get("final")]
    margins = [r["home_score"] - r["away_score"] for r in cfb_rows]
    totals = [r["home_score"] + r["away_score"] for r in cfb_rows]
    sig_m, sig_t = 15.7, 15.4          # CFB residual SDs vs Pinnacle close, 2026 development data (n=506)
    cfb_m, cfb_t = NZ.cfb(margins, totals, sig_m, sig_t)
    norm = {("nfl", "spreads"): nfl_m, ("nfl", "totals"): nfl_t, ("cfb", "spreads"): cfb_m, ("cfb", "totals"): cfb_t}
    rows = []
    for (e, mk, t, b), sd in q.items():
        s, h, a, k = meta[e]
        if not s or t >= k:
            continue
        A, Bs = SIDES[mk]
        if A not in sd or Bs not in sd:
            continue
        (pa, ra), (pb, rb) = sd[A], sd[Bs]
        z = norm[(s, mk)].quote(A, pa, ra, Bs, pb, rb)
        if not z:
            continue
        rows.append({"e": e, "s": s, "m": mk, "t": t, "b": b, "pt": pa, "pa": ra, "pb": rb, "mu": round(z["mu"], 3),
                     "p": round(z["p_a_nopush"], 4)})
    ev = {e: {"sport": s, "home": h, "away": a, "kickoff": k, "final": fin.get(e)} for e, (s, h, a, k) in meta.items() if s}
    import numpy as _np
    keys_m = _np.abs(cfb_m.values); keys_t = cfb_t.values
    return rows, ev, {"cfb_margin_sigma": sig_m, "cfb_total_sigma": sig_t, "cfb_finals": len(cfb_rows),
                      "cfb_margin_weights": NZ.D.learn_weights(_np.asarray(margins), NZ.D.M_RANGE, sig_m, absolute=True),
                      "cfb_total_weights": {},
                      "confidence": {"nfl": nfl_m.confidence, "cfb": cfb_m.confidence}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", nargs="+", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows, ev, info = build(a.shards, a.results)
    with gzip.open(a.out, "wt") as f:
        json.dump({"rows": rows, "events": ev, "info": info}, f)
    print(len(rows), "rows;", len(ev), "events;", sum(1 for v in ev.values() if v["final"]), "with finals;", info)


if __name__ == "__main__":
    main()
