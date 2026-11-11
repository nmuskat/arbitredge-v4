"""
lab_v4/realism_runs.py -- every ledger the realism audit needs, built in parallel (resumable).

  python -m lab_v4.realism_runs --market-db market.db --rankings DIR --results DIR --out DIR [--workers 4]
"""
import argparse
import multiprocessing as mp
import os
import pickle

from lab_v4.config import CONFIGS
from lab_v4.ledger import build

OTHER_REC = ("REC_CASUAL", "NARRATIVE_CHASER", "TEAM_LOYAL", "UNDERDOG_CONTRARIAN", "SELECTIVE_PRICE_SENSITIVE")
JOBS = ([("A", 0, None, "A_s0")] +
        [("A", s, None, f"A_all_s{s}") for s in (1, 2)] +
        [("A", s, ("HIGH_STAKES_REC",), f"A_hs_s{s}") for s in (1, 2)] +
        [("A", s, ("SHARP",), f"A_sharp_s{s}") for s in (1, 2)] +
        [("A", s, OTHER_REC, f"A_otherrec_s{s}") for s in (1, 2)] +
        [("R", s, None, f"R_s{s}") for s in (0, 1, 2)] +
        [("R_SHARP", 0, None, "RSHARP_s0"), ("R_MAXWIN", 0, None, "RMAXWIN_s0")])


def one(args):
    a, (cfg, seed, rekey, name) = args
    p = os.path.join(a["out"], f"ledger_{name}.pkl")
    if os.path.exists(p):
        return name, "exists"
    led = build(a["market_db"], a["rankings"], a["results"], cfg, seed, rekey=rekey)
    led["name"], led["rekey"] = name, rekey
    pickle.dump(led, open(p + ".tmp", "wb"))
    os.replace(p + ".tmp", p)
    return name, "built"


def main(argv=None):
    ap = argparse.ArgumentParser()
    for k in ("market-db", "rankings", "results", "out"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--workers", type=int, default=4)
    a = vars(ap.parse_args(argv))
    os.makedirs(a["out"], exist_ok=True)
    with mp.Pool(a["workers"]) as pool:
        for name, st in pool.imap_unordered(one, [(a, j) for j in JOBS]):
            print(name, st, flush=True)


if __name__ == "__main__":
    main()
