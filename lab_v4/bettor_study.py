"""
lab_v4/bettor_study.py -- the complete individual-bettor FADE/TAIL study.  RESEARCH (development data).

  python -m lab_v4.bettor_study --ledger-dir DIR --market-db market.db --market-pkl market.pkl --out DIR

Group rules are fixed here, before any evaluation, and never tuned on the evaluation weeks.
"""
import argparse
import collections
import csv
import gzip
import json
import os
import pickle
import sqlite3

from lab_v4 import bettors as BT

ARCHS = ("HIGH_STAKES_REC", "REC_CASUAL", "SHARP", "TEAM_LOYAL", "NARRATIVE_CHASER", "UNDERDOG_CONTRARIAN",
         "SELECTIVE_PRICE_SENSITIVE")
CELLS = (("spread", "favourite"), ("spread", "underdog"), ("total", "over"), ("total", "under"),
         ("moneyline", "favourite"), ("moneyline", "underdog"))


def rules(k=15, rank="z"):
    r = {}
    for d, lab in (("best", "TOP"), ("worst", "BOTTOM")):
        r[f"{lab}{k}_ALL"] = {"scope": lambda w: True, "direction": d, "size": "k", "k": k}
        r[f"{lab}_DECILE_ALL"] = {"scope": lambda w: True, "direction": d, "size": "decile", "k": k}
        for a in ARCHS:
            r[f"{lab}{k}_{a}"] = {"scope": (lambda a: lambda w: w["archetype"] == a)(a), "direction": d, "size": "k", "k": k}
        for m, sd in CELLS:
            r[f"{lab}{k}_{m}_{sd}"] = {"scope": (lambda m, sd: lambda w: w["market"] == m and w["side_type"] == sd)(m, sd),
                                       "direction": d, "size": "k", "k": k, "n_min": 3}
    for v in r.values():
        v["rank"] = rank
    return r


def consensus(rows, min_agree=3):
    """Phase 4: per (sport, week, game-market, side), distinct selected bettors from BEST vs WORST groups."""
    cell = collections.defaultdict(lambda: {"best": set(), "worst": set(), "row": None})
    for r in rows:
        pol = "worst" if r["rule"].startswith("BOTTOM") else "best"
        k = (r["sport"], r["week"], r["gid"], r["market"], r["side"])
        cell[k][pol].add(r["bettor_uid"])
        if cell[k]["row"] is None or (r.get("tail") and r.get("fade")):
            cell[k]["row"] = r
    groups = collections.defaultdict(list)
    for k, c in cell.items():
        r = c["row"]
        nb, nw = len(c["best"]), len(c["worst"])
        opp = cell.get(k[:4] + (BT.SIDE_OPP[k[4]],))
        conflict = bool(opp and (opp["best"] or opp["worst"]))
        if nw >= min_agree and not nb:
            groups[f"FADE: >= {min_agree} worst bettors agree, no best bettor with them"].append(dict(r, rule="C"))
        if nb >= min_agree and not nw:
            groups[f"TAIL: >= {min_agree} best bettors agree, no worst bettor with them"].append(dict(r, rule="C"))
        if nb and nw:
            groups["MIXED: best and worst bettors on the same side"].append(dict(r, rule="C"))
        if conflict and nb and not nw and opp["worst"] and not opp["best"]:
            groups["DISAGREE: best bettors here, worst bettors on the other side (tail best)"].append(dict(r, rule="C"))
    return groups


def side_type_baseline(ws, eval_weeks_min=3):
    """Phase 5 (C: market explanation): tail EVERY bettor wager of each side type in the evaluation weeks at its
    own accepted price vs decision fair -- what the side type itself did, independent of who bet it."""
    out = {}
    for sport in ("nfl", "cfb"):
        for m, sd in CELLS:
            xs = [w for w in ws if w["sport"] == sport and w["week"] >= eval_weeks_min and w["market"] == m
                  and w["side_type"] == sd and w["exp_units"] is not None]
            if not xs:
                continue
            st_ = sum(w["stake"] for w in xs)
            out[f"{sport}|{m}|{sd}"] = {"wagers": len(xs), "roi": sum(w["customer_pnl"] for w in xs) / st_,
                                        "excess": (sum(w["customer_pnl"] for w in xs) - sum(w["exp_units"] * w["stake"] for w in xs)) / st_}
    return out


def run_ledger(name, led, prices, finals, variants=True):
    ws = BT.flatten(led)
    res = {"name": name}
    base = BT.walk_forward(ws, prices, finals, rules(15))
    res["walk_forward"] = summarize_wf(base)
    res["consensus"] = {g: {d: BT.evaluate(rs, d) for d in ("tail", "fade")} for g, rs in consensus(base).items()}
    if variants:
        res["variants"] = {}
        res["variants"]["k=10"] = summarize_wf(BT.walk_forward(ws, prices, finals, rules(10)))
        res["variants"]["first eval week 5"] = summarize_wf(BT.walk_forward(ws, prices, finals, rules(15), first_eval_week=5))
        cap = sorted(w["stake"] for w in ws)[int(0.99 * len(ws))]
        ws_nb = [w for w in ws if w["stake"] <= cap]          # selection AND evaluation without the largest 1% of wagers
        res["variants"]["ranked by net $ P&L"] = summarize_wf(BT.walk_forward(ws, prices, finals, rules(15, "pnl")))
        res["variants"]["ranked by ROI"] = summarize_wf(BT.walk_forward(ws, prices, finals, rules(15, "roi")))
        res["variants"]["largest 1% of wagers removed"] = summarize_wf(BT.walk_forward(ws_nb, prices, finals, rules(15)))
    return res, base, ws


def summarize_wf(rows):
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["rule"], r["sport"])].append(r)
    out = {}
    for (rule, sport), rs in sorted(by.items()):
        out[f"{rule}|{sport}"] = {"selections": len(rs), "bettors": len({r["bettor_uid"] for r in rs}),
                                  "tail": BT.evaluate(rs, "tail"), "fade": BT.evaluate(rs, "fade")}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger-dir", required=True)
    ap.add_argument("--market-db", required=True)
    ap.add_argument("--market-pkl", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    conn = sqlite3.connect(a.market_db)
    prices = BT.Prices(conn, pickle.load(open(a.market_pkl, "rb")))
    report = {}
    for name, var in (("A_s0", True), ("A_all_s1", False), ("A_all_s2", False), ("R_s0", False)):
        led = pickle.load(open(os.path.join(a.ledger_dir, f"ledger_{name}.pkl"), "rb"))
        res, base, ws = run_ledger(name, led, prices, led["finals"], variants=var)
        if name == "A_s0":
            summ, long = BT.bettor_table(ws)
            res["bettors"] = len(summ)
            res["reconcile"] = {"sum_bettor_pnl": round(sum(s["pnl"] for s in summ.values()), 2),
                                "ledger_pnl": round(sum(w["customer_pnl"] for w in ws), 2),
                                "sum_bettor_staked": round(sum(s["staked"] for s in summ.values()), 2),
                                "ledger_staked": round(sum(w["stake"] for w in ws), 2),
                                "sum_bettor_wagers": sum(s["wagers"] for s in summ.values()), "ledger_wagers": len(ws)}
            sh = BT.shrunk(list(summ.values()))
            for u, s in summ.items():
                s.update(sh["bettors"].get(u, {"shrunk_excess": None, "B": None}))
            res["shrinkage"] = {"tau2": sh["tau2"], "mean_se2": sh["mean_se2"]}
            res["split_half"] = BT.split_half(ws)
            res["side_type_baseline"] = side_type_baseline(ws)
            ranked = sorted(summ.values(), key=lambda s: s["pnl"])
            res["top20_profit"] = ranked[-20:][::-1]
            res["bottom20_profit"] = ranked[:20]
            with gzip.open(os.path.join(a.out, "bettor_summary.csv.gz"), "wt", newline="") as f:
                wr = csv.DictWriter(f, fieldnames=list(next(iter(summ.values())).keys()))
                wr.writeheader()
                wr.writerows(summ.values())
            with gzip.open(os.path.join(a.out, "bettor_breakdown.csv.gz"), "wt", newline="") as f:
                wr = csv.DictWriter(f, fieldnames=list(long[0].keys()))
                wr.writeheader()
                wr.writerows(long)
        report[name] = res
        print(name, "done", flush=True)
        json.dump(report, open(os.path.join(a.out, "bettor_study.json"), "w"), default=str)


if __name__ == "__main__":
    main()
