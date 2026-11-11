"""
intel_v7/prospective.py -- the FROZEN prospective evaluation (docs_v4/V7_PROSPECTIVE_SPEC.md), implemented in advance.

  python -m intel_v7.prospective --need-dir DIR --market-db market.db --results DIR [--rankings DIR] --out report.json

Reads only frozen v7intel_* records with written_before_final = true and the matching CONFIG_HASH.
"""
import argparse
import json
import random
import sqlite3

from intel_v7 import site as SITE
from intel_v7.classify import best_price
from intel_v7.config import CONFIG_HASH
from intel_v7.evaluate import close_state, outcome
from intel_v7.history import SIDES, History, view
from sportsbook_v4.util import to_epoch

SPEC_HASH_CONFIG = "622c296b99ce"
BOOT = 2000


def rows_from_records(conn, recs, finals):
    out = []
    for r in recs:
        c = r["card"]
        if not r.get("written_before_final") or c.get("config_hash") != SPEC_HASH_CONFIG:
            continue
        hist = History(conn, c["sport"], c["event_id"], c["market"])
        close = close_state(hist) if hist.kickoff else None
        t = to_epoch(c["as_of"])
        _, books = hist.snap_at(t)
        dv = view(books)
        row = {"game": (c["sport"], c["event_id"]), "signal": c["signal_or_pass"] == "SIGNAL", "lean": bool(c.get("side")),
               "no_close": close is None, "bets": {}}
        if close is None or "pro_mu" not in dv:
            out.append(row)
            continue
        fin = finals.get((c["sport"], c["event_id"]))
        bp = c.get("best_observed_executable_price")
        if c.get("side") and bp:
            row["bets"]["v7"] = outcome(hist, c["side"], bp["point"], bp["price"], dv, close, fin)
        bps = {s: best_price(hist, t, s, dv["pro_mu"]) for s in SIDES[c["market"]]}
        if all(bps.values()):
            s = max(bps, key=lambda x: bps[x]["ev_vs_pro_fair"])
            row["bets"]["shop"] = outcome(hist, s, bps[s]["point"], bps[s]["price"], dv, close, fin)
        out.append(row)
    return out


def _boot_lb(pairs, f, seed):
    by = {}
    for g, x in pairs:
        by.setdefault(g, []).append(x)
    keys = sorted(by)
    if not keys:
        return None, None
    rng = random.Random(seed)
    vals = sorted(v for v in (f([x for k in (keys[rng.randrange(len(keys))] for _ in keys) for x in by[k]])
                              for _ in range(BOOT)) if v is not None)
    return f([x for _, x in pairs]), (vals[int(0.05 * len(vals))] if vals else None)


def evaluate(rows):
    sig = [(r["game"], r["bets"]["v7"]) for r in rows if r["signal"] and "v7" in r["bets"]]
    shop = {r["game"]: r["bets"]["shop"] for r in rows if "shop" in r["bets"]}
    mean = lambda k: (lambda xs: (lambda v: sum(v) / len(v) if v else None)([x[k] for x in xs if x.get(k) is not None]))  # noqa: E731
    m1, m1_lb = _boot_lb(sig, mean("retail_move_toward"), 1)
    m2, m2_lb = _boot_lb(sig, mean("clv_consensus_close"), 2)
    diff_pairs = [(g, {"d": b["clv_consensus_close"] - shop[g]["clv_consensus_close"]}) for g, b in sig if g in shop]
    d, d_lb = _boot_lb(diff_pairs, mean("d"), 3)
    graded = [(g, b) for g, b in sig if b["result"] in ("win", "loss")]
    roi, roi_lb = _boot_lb(graded, lambda xs: sum(b["units"] for b in xs) / len(xs) if xs else None, 4)
    n = len(sig)
    verdict = ("INCONCLUSIVE (fewer than 100 SIGNALs)" if n < 100 else
               "CANDIDATE BETTING EDGE (H1 and H2); needs a second prospective season" if (m1_lb or 0) > 0 and (m2_lb or 0) > 0 and (d_lb or 0) > 0 else
               "PRICE DISCOVERY ONLY (H1); not a betting edge" if (m1_lb or 0) > 0 else
               "NOT VALIDATED: observational tool only")
    return {"config_hash": SPEC_HASH_CONFIG, "signals": n, "records": len(rows),
            "excluded_no_close": sum(r["no_close"] for r in rows),
            "H1_retail_move_toward": m1, "H1_lower95": m1_lb, "H2_clv_consensus": m2, "H2_lower95": m2_lb,
            "H2_vs_shop": d, "H2_vs_shop_lower95": d_lb, "roi": roi, "roi_lower95": roi_lb,
            "graded": len(graded), "profitable_claim_allowed": n >= 300 and (roi_lb or 0) > 0 and (m2_lb or 0) > 0 and (d_lb or 0) > 0,
            "verdict": verdict}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--need-dir", required=True)
    ap.add_argument("--market-db", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--rankings")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.market_db)
    recs = list(SITE.existing(a.need_dir).values())
    from intel_v7.evaluate import load_finals
    gl = [(r["card"]["sport"], r["card"]["event_id"], to_epoch(r["card"]["kickoff"]), r["card"]["home"], r["card"]["away"]) for r in recs]
    rep = evaluate(rows_from_records(conn, recs, load_finals(a.rankings, a.results, gl)))
    rep["current_config_hash"] = CONFIG_HASH
    json.dump(rep, open(a.out, "w"), indent=1, default=str)
    print(json.dumps(rep, indent=1, default=str))


if __name__ == "__main__":
    main()
