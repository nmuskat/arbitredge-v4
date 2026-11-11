"""
lab_v4/ledger.py -- dump the full wager-level ledger of one configuration x seed (with audit fields) to a pickle.

  python -m lab_v4.ledger --market-db market.db --rankings DIR --results DIR --config A --seed 0 --out ledger.pkl
"""
import argparse
import pickle
import sqlite3

from collect_v4 import backfill as B
from intel_v7.evaluate import load_finals
from lab_v4.config import CONFIGS
from lab_v4.experiment import game_list
from lab_v4.season import run_season
from tools_v4.backfill_replay import load_polls


def build(market_db, rankings, results, config, seed, rekey=None, cfg=None):
    live = sqlite3.connect(market_db)
    polls = B.poll_snapshots(load_polls(rankings))
    gl = game_list(live, polls)
    finals = load_finals(rankings, results, gl)
    c = cfg or CONFIGS[config]
    facts = None
    if c.get("v2"):
        from intel_v7.history import games as store_games
        from lab_v4.behavior_v2 import RealFacts
        allg = [(s, e, k, h, a) for s in ("nfl", "cfb") for e, k, h, a in store_games(live, s)]
        allf = load_finals(rankings, results, allg)          # finals only ever used for EARLIER games' form
        facts = {s: RealFacts(s, allg, allf) for s in ("nfl", "cfb")}
    weeks = run_season(live, polls, finals, c, seed, rekey=rekey, record=True, facts=facts)
    return {"weeks": weeks, "finals": finals, "games": gl, "config": config, "seed": seed}


def main(argv=None):
    ap = argparse.ArgumentParser()
    for k in ("market-db", "rankings", "results", "out"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--config", default="A")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    pickle.dump(build(a.market_db, a.rankings, a.results, a.config, a.seed), open(a.out, "wb"))


if __name__ == "__main__":
    main()
