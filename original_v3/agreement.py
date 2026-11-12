"""
original_v3/agreement.py -- historical MARKET NEED x SYNTHETIC SHARP agreement test (EXPLORATORY; research).

Two sharp-position sources, never mixed:
  GENUINE        production synthetic-book ledger (release v4-book): a wager counts only if accepted by the cutoff
                 AND its immutable file was PUBLISHED by the cutoff (release upload time). Latest-published run per
                 game at the cutoff.
  RECONSTRUCTED  the full-season replay of the same production engine (lab ledger, generated 2026-11-11): positions
                 re-simulated after the fact -- reported separately, NEVER as frozen records.
Market NEED: original_v3 replays (spreads: original engine; totals: mi_totals.v1), both freshness rules.
Bets graded by original_v3.evaluate.Grader (best real price at the decision time; consensus-close CLV).

  python -m original_v3.agreement --orig DIR --prodbook DIR --replay-ledger FILE --market-db DB --out FILE
"""
import argparse
import collections
import glob
import gzip
import json
import os
import pickle
import random
import sqlite3
import statistics as st

from original_v3.evaluate import Grader, summ
from original_v3.sharp import classify, position
from sportsbook_v4.util import to_epoch

OPP = {"home": "away", "away": "home", "over": "under", "under": "over"}


def load_prodbook(d):
    pub = {}
    for line in open(os.path.join(d, "assets.txt")):
        _, name, created = line.split()
        pub[name] = created.replace("Z", ".000000Z") if "." not in created else created
    wagers, run_first = [], {}
    for p in sorted(glob.glob(os.path.join(d, "book_*.json.gz"))):
        name = os.path.basename(p)
        doc = json.load(gzip.open(p, "rt"))
        t = pub[name]
        rid = doc["run"]["run_id"]
        run_first[rid] = min(run_first.get(rid, t), t)
        for w in doc["wagers"]:
            w = dict(w, _published_at=t)
            wagers.append(w)
    return wagers, run_first


def index(wagers):
    by = collections.defaultdict(list)
    for w in wagers:
        if w["market"] in ("spread", "total"):
            by[(w["sport"], w["canonical_game_id"])].append(w)
    return by


def genuine_at(by, run_first, sport, eid, market, cutoff):
    ws = [w for w in by.get((sport, eid), []) if w["accepted_at"] <= cutoff and w["_published_at"] <= cutoff]
    if not ws:
        return [], [], None
    runs = {w["run_id"] for w in ws}
    rid = max(runs, key=lambda r: run_first[r])
    all_ = [w for w in ws if w["run_id"] == rid]
    return [w for w in all_ if w["market"] == market and w["archetype"] == "SHARP"], [w for w in all_ if w["market"] == market], rid


def recon_at(by, sport, eid, market, cutoff):
    ws = [w for w in by.get((sport, eid), []) if w["market"] == market and w["accepted_at"] <= cutoff]
    return [w for w in ws if w["archetype"] == "SHARP"], ws


def fin_need(ws, market):
    if not ws:
        return None
    from lab_v4.concentration import need_side
    return need_side(ws, market)[0]


def main(argv=None):
    ap = argparse.ArgumentParser()
    for k in ("orig", "prodbook", "replay-ledger", "market-db", "out"):
        ap.add_argument("--" + k, required=True)
    a = ap.parse_args(argv)
    gw, run_first = load_prodbook(a.prodbook)
    gby = index(gw)
    led = pickle.load(open(a.replay_ledger, "rb"))
    rby = index([dict(w, sport=wk["sport"]) for wk in led["weeks"] for w in wk["wagers"]])
    conn = sqlite3.connect(f"file:{a.market_db}?mode=ro", uri=True)
    rows = []
    for cad in ("v3", "v4"):
        for mk, fn in (("spread", f"full_{cad}.pkl"), ("total", f"mi_{cad}.pkl")):
            rep = pickle.load(open(os.path.join(a.orig, fn), "rb"))
            g = Grader(conn, rep["finals"])
            for x in rep["results"]:
                if "error" in x:
                    continue
                m = rep["meta"][x["gid"]]
                sp, eid, cut = m["sport"], m["event_id"], x["as_of"]
                need = x["side"] if x.get("side") in ("home", "away", "over", "under") else None
                t = to_epoch(cut)
                r = {"cad": cad, "market": mk, "sport": sp, "eid": eid, "label": x["as_of_label"], "need": need,
                     "strength": x.get("strength"), "regime": x.get("regime")}
                for src in ("genuine", "recon"):
                    if src == "genuine":
                        sw, allw, rid = genuine_at(gby, run_first, sp, eid, mk, cut)
                        if rid is None:
                            r[src] = {"available": False}
                            continue
                    else:
                        if (sp, eid) not in rby:
                            r[src] = {"available": False}
                            continue
                        sw, allw = recon_at(rby, sp, eid, mk, cut)
                    pos = position(sw, mk)
                    cat, why = classify(need, pos)
                    r[src] = {"available": True, "pos": {k: pos.get(k) for k in ("status", "direction", "strength", "tickets",
                                                                                  "bettors", "largest_wager_share")},
                              "cat": cat, "why": why, "fin_need": fin_need(allw, mk)}
                    if pos.get("direction"):
                        r[src]["sharp_bet"] = g.bet(sp, eid, pos["direction"], t)
                r["need_bet"] = g.bet(sp, eid, need, t) if need else None
                rows.append(r)
    pickle.dump(rows, open(a.out, "wb"))
    print(len(rows), "rows")


if __name__ == "__main__":
    main()
