"""
lab_v4/reconcile.py -- independent re-settlement of every wager (own implementation of the payout rules, written
separately from sportsbook_v4.settlement) for the replay ledger and the PRODUCTION settlement files.
"""
import collections
import glob
import gzip
import json


def payout_profit(stake, price):
    """Profit on a winning bet at American odds (stake returned separately)."""
    return stake * price / 100.0 if price > 0 else stake * 100.0 / -price


def result(market, side, line, hs, as_):
    if market == "spread":
        v = (hs - as_) + line if side == "home" else (as_ - hs) + line
    elif market == "total":
        v = (hs + as_) - line if side == "over" else line - (hs + as_)
    else:
        v = (hs - as_) if side == "home" else (as_ - hs)
    return "win" if v > 0 else "loss" if v < 0 else "push"


def customer_pnl(stake, price, res):
    return payout_profit(stake, price) if res == "win" else -stake if res == "loss" else 0.0


def replay(led):
    out = {"wagers": 0, "mismatch_result": 0, "mismatch_pnl": 0, "max_abs_diff": 0.0, "unsettled": 0,
           "totals": collections.defaultdict(lambda: [0, 0.0, 0.0])}
    for wk in led["weeks"]:
        for w in wk["wagers"]:
            out["wagers"] += 1
            f = led["finals"].get((wk["sport"], w["canonical_game_id"]))
            if f is None:
                out["unsettled"] += 1
                continue
            line = w["accepted_line"] if w["market"] != "moneyline" else 0.0
            r = result(w["market"], w["side"], line, f[0], f[1])
            p = customer_pnl(w["stake"], w["accepted_price"], r)
            out["mismatch_result"] += r != w["result"]
            d = abs(p - w["customer_pnl"])
            out["mismatch_pnl"] += d > 0.005
            out["max_abs_diff"] = max(out["max_abs_diff"], d)
            for k in ((wk["sport"], "all"), (wk["sport"], w["market"]), (wk["sport"], w["archetype"]), ("all", "all")):
                t = out["totals"][k]
                t[0] += 1
                t[1] += w["stake"]
                t[2] += p
    out["totals"] = {f"{a}|{b}": {"wagers": v[0], "handle": round(v[1], 2), "customer_pnl": round(v[2], 2),
                                  "book_pnl": round(-v[2], 2), "hold": round(-v[2] / v[1], 5)}
                     for (a, b), v in sorted(out["totals"].items())}
    return out


def production(book_dir, settle_dir):
    """Re-settle every PRODUCTION settlement record; tie each to its ledger wager (stake, price, line, side)."""
    wagers, runs = {}, {}
    for p in glob.glob(f"{book_dir}/*.json.gz"):
        d = json.load(gzip.open(p, "rt"))
        runs[d["run"]["run_id"]] = d["run"]
        for w in d["wagers"]:
            wagers[w["wager_id"]] = w
    recs = {}
    for p in sorted(glob.glob(f"{settle_dir}/*.json.gz")):
        for r in json.load(gzip.open(p, "rt"))["records"]:
            recs[r["settlement_id"]] = r
    latest = {}
    superseded = {r["supersedes"] for r in recs.values() if r.get("supersedes")}
    for r in recs.values():
        if r["settlement_id"] not in superseded:
            latest[r["wager_id"]] = r
    out = {"ledger_wagers": len(wagers), "runs": len(runs), "settlement_records": len(recs),
           "settled_wagers": len(latest), "missing_wager": 0, "term_mismatch": 0, "result_mismatch": 0,
           "pnl_mismatch_cents": 0, "max_abs_diff_cents": 0}
    by = collections.defaultdict(lambda: [0, 0, 0])
    for wid, r in latest.items():
        w = wagers.get(wid)
        if w is None:
            out["missing_wager"] += 1
            continue
        if (round(w["stake"] * 100) != r["stake_cents"] or float(w["accepted_price"]) != float(r["accepted_price"])
                or w["side"] != r["side"] or w["market"] != r["market"]):
            out["term_mismatch"] += 1
        line = float(r["accepted_line"]) if r["market"] != "moneyline" else 0.0
        res = result(r["market"], r["side"], line, r["home_score"], r["away_score"])
        out["result_mismatch"] += res != r["result"]
        cents = round(customer_pnl(r["stake_cents"] / 100, r["accepted_price"], res) * 100)
        diff = abs(cents - r["pnl_cents"])
        out["pnl_mismatch_cents"] += diff > 1
        out["max_abs_diff_cents"] = max(out["max_abs_diff_cents"], diff)
        k = (r["sport"], runs.get(w["run_id"], {}).get("window_start", "?")[:10], w["run_id"][:12])
        by[k][0] += 1
        by[k][1] += r["stake_cents"]
        by[k][2] += r["pnl_cents"]
    out["by_run"] = {"|".join(k): {"settled": v[0], "handle": v[1] / 100, "book_pnl": -v[2] / 100} for k, v in sorted(by.items())}
    out["engines"] = dict(collections.Counter(r.get("engine_version") for r in runs.values()))
    return out
