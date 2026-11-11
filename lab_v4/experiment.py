"""
lab_v4/experiment.py -- run the controlled experiment.  RESEARCH (development data).

  python -m lab_v4.experiment --market-db market.db --rankings DIR --results DIR --out DIR [--configs ABCDE] [--seeds 0,1,2]

Writes OUT/market.pkl (shared market outcomes) and OUT/run_<config>_<seed>.json (one per run; skipped if present).
"""
import argparse
import json
import os
import pickle
import sqlite3
import time

from collect_v4 import backfill as B
from intel_v7.evaluate import load_finals
from lab_v4.config import CONFIGS, SEEDS
from lab_v4.measure import market_outcomes, summarize_run
from lab_v4.season import LIVE_TO, run_season
from tools_v4.backfill_replay import load_polls, select_games


def game_list(live, polls):
    out = []
    for sport in ("nfl", "cfb"):
        for ws, we in B.windows(sport, live_from=LIVE_TO):
            games, _ = select_games(live, sport, ws, we, polls)
            out += [(sport, g["event_id"], g["kickoff"], g["home"], g["away"]) for g in games]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-db", required=True)
    ap.add_argument("--rankings", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--configs", default="ABCDE")
    ap.add_argument("--seeds", default=",".join(map(str, SEEDS)))
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    live = sqlite3.connect(a.market_db)
    polls = B.poll_snapshots(load_polls(a.rankings))
    gl = game_list(live, polls)
    finals = load_finals(a.rankings, a.results, gl)
    mp = os.path.join(a.out, "market.pkl")
    if os.path.exists(mp):
        mo = pickle.load(open(mp, "rb"))
    else:
        t0 = time.time()
        mo = market_outcomes(live, [(s, e, k) for s, e, k, _, _ in gl], finals)
        pickle.dump(mo, open(mp, "wb"))
        print(f"market outcomes: {len(mo)} game-markets in {time.time() - t0:.0f}s", flush=True)
    json.dump({"games": len(gl), "finals": len(finals)}, open(os.path.join(a.out, "meta.json"), "w"))
    for c in a.configs:
        for s in map(int, a.seeds.split(",")):
            p = os.path.join(a.out, f"run_{c}_{s}.json")
            if os.path.exists(p):
                continue
            t0 = time.time()
            weeks = run_season(live, polls, finals, CONFIGS[c], s)
            summ = summarize_run(weeks, mo)
            summ.update(config=c, seed=s, label=CONFIGS[c]["label"], seconds=round(time.time() - t0, 1))
            json.dump(summ, open(p, "w"))
            print(f"{c} seed {s}: {summ['hold']['all|all']} {summ['seconds']}s", flush=True)


if __name__ == "__main__":
    main()
