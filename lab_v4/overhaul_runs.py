"""
lab_v4/overhaul_runs.py -- ledgers for the bettor-behaviour overhaul (V4b2 at 20k / 10k / 5k customers x 3 seeds).
Configuration A (production) ledgers are re-used from the realism audit (same engine, same seeds).

  python -m lab_v4.overhaul_runs --market-db market.db --rankings DIR --results DIR --out DIR [--workers 4]
"""
import argparse
import multiprocessing as mp
import os
import pickle

from lab_v4.ledger import build

JOBS = [(c, s, f"{c}_s{s}") for c in ("B2_20K", "B2_10K", "B2_5K", "TB_PUBLIC", "TB_EMOTION", "TB_PRICE")
        for s in (0, 1, 2)]


def one(args):
    a, (cfg, seed, name) = args
    p = os.path.join(a["out"], f"ledger_{name}.pkl")
    if os.path.exists(p):
        return name, "exists"
    led = build(a["market_db"], a["rankings"], a["results"], cfg, seed)
    led["name"] = name
    pickle.dump(led, open(p + ".tmp", "wb"))
    os.replace(p + ".tmp", p)
    return name, "built"


def main(argv=None):
    ap = argparse.ArgumentParser()
    for k in ("market-db", "rankings", "results", "out"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only")
    a = vars(ap.parse_args(argv))
    os.makedirs(a["out"], exist_ok=True)
    jobs = [j for j in JOBS if not a["only"] or j[2] in a["only"].split(",")]
    with mp.Pool(a["workers"]) as pool:
        for name, st in pool.imap_unordered(one, [(a, j) for j in jobs]):
            print(name, st, flush=True)


if __name__ == "__main__":
    main()
