"""
edge_research/run.py -- executes the FIXED plan (edge_research/plan.py) on the real collected quote store.

  python -m edge_research.run --market-db market.db --rankings DIR --results DIR --out DIR

Point in time: every signal uses only fetches at or before its decision fetch; closes and outcomes are used only
for grading. Read-only on the quote store (sqlite mode=ro). No API calls.
"""
import argparse
import json
import math
import os
import random
import sqlite3
import statistics as st
from collections import defaultdict

from edge_research.plan import PLAN, PLAN_HASH
from edge_v4 import pricing
from intel_v7.history import History, games, operator_of, view
from need_v6.market import normalizer
from sportsbook_v4.util import from_epoch

SIGN = {"home": 1, "over": 1, "away": -1, "under": -1}
SIDES = {"spread": ("home", "away"), "total": ("over", "under")}
OPP = {"home": "away", "away": "home", "over": "under", "under": "over"}
PRO_BOOKS = {"pinnacle": ["pinnacle"], "betonline_group": ["betonlineag", "lowvig"]}


def payout(price):
    return price / 100.0 if price > 0 else 100.0 / -price


def side_quotes(books, market, side, exclude=()):
    """[(book, point, price)] for `side` from every fresh quote."""
    out = []
    for bk, q in books.items():
        if bk in exclude or q["mu"] is None:
            continue
        a, b = SIDES[market]
        if side == a:
            out.append((bk, q["point"], q["price_a"]))
        else:
            out.append((bk, -q["point"] if market == "spread" else q["point"], q["price_b"]))
    return out


def ev_at(nz, mu, side, point, price):
    pw, pp = nz.prob_at(mu, side, point)
    return pw * payout(price) - (1 - pw - pp)


def best(nz, mu_ref, books, market, side, exclude=()):
    qs = side_quotes(books, market, side, exclude)
    if not qs:
        return None
    return sorted(((ev_at(nz, mu_ref, side, pt, pr), bk, pt, pr) for bk, pt, pr in qs), key=lambda r: (-r[0], r[1]))[0]


def result(sport, market, side, point, price, finals_hs):
    hs, as_ = finals_hs
    if market == "spread":
        m = (hs - as_) * (1 if side == "home" else -1) + point
    else:
        m = (hs + as_ - point) * (1 if side == "over" else -1)
    return payout(price) if m > 0 else (0.0 if m == 0 else -1.0)


def devig_a(q):
    try:
        return pricing.devig([q["price_a"], q["price_b"]])[0]
    except ValueError:
        return None


class Game:
    def __init__(self, conn, sport, eid, market, live_start):
        self.h = History(conn, sport, eid, market)
        self.sport, self.market, self.eid = sport, market, eid
        self.nz = normalizer(sport, market)
        self.live_start = live_start

    def at(self, t, max_age):
        f, b = self.h.snap_at(t)
        if f is None or t - f > max_age:
            return None, None, None
        v = view(b)
        return f, b, v


def bet_record(g, sig, side, books, mu_dec_ref, close_mu, dec_cons_mu, finals, exclude=(), pro_close=None):
    b1 = best(g.nz, mu_dec_ref, books, g.market, side, exclude)
    b2 = best(g.nz, mu_dec_ref, books, g.market, OPP[side], exclude)
    if b1 is None:
        return None
    _, bk, pt, pr = b1
    clv = ev_at(g.nz, close_mu, side, pt, pr)
    rec = {"hyp": sig, "sport": g.sport, "market": g.market, "event_id": g.eid, "side": side, "book": bk,
           "point": pt, "price": pr, "clv": clv,
           "move": (close_mu - dec_cons_mu) * SIGN[side] if dec_cons_mu is not None else None,
           "dir_clv": (clv - ev_at(g.nz, close_mu, OPP[side], b2[2], b2[3])) if b2 else None}
    if pro_close is not None:      # SECONDARY (exploratory; circular for Pinnacle-derived signals if Pinnacle held)
        rec["clv_pro"] = ev_at(g.nz, pro_close, side, pt, pr)
    fin = finals.get((g.sport, g.eid))
    if fin and None not in fin:
        rec["profit"] = result(g.sport, g.market, side, pt, pr, fin)
    return rec


def evaluate_game(g, finals):
    P = PLAN
    ko = g.h.kickoff
    if ko is None:
        return [], [], {"status": "no quotes"}
    fd, bd, vd = g.at(ko - P["decision_offset_s"], P["decision_max_age_s"])
    fc, bc, vc = g.at(ko - 1, P["close_max_before_kick_s"])
    cov = {"has_dec": fd is not None, "has_close": fc is not None,
           "pro_at_dec": bool(vd and "pro_mu" in vd), "retail_at_dec": bool(vd and "retail_mu" in vd),
           "n_fetches": len(g.h.snaps), "final": (g.sport, g.eid) in finals}
    if fd is None or fc is None or fc - fd < P["close_min_after_dec_s"] or vc.get("consensus_mu") is None:
        cov["status"] = "no decision or close"
        return [], [], cov
    cov["status"] = "ok"
    era = "archive" if fd < g.live_start else "live"
    close_mu, cons_d = vc["consensus_mu"], vd.get("consensus_mu")
    recs = []
    pro_mu, ret_mu = vd.get("pro_mu"), vd.get("retail_mu")
    a, b = SIDES[g.market]
    # H1 pro disagreement
    if pro_mu is not None and ret_mu is not None and abs(pro_mu - ret_mu) >= P["H1_gap_pts"]:
        r = bet_record(g, "H1", a if pro_mu > ret_mu else b, bd, pro_mu, close_mu, cons_d, finals, pro_close=vc.get('pro_mu'))
        if r:
            r["gap"] = pro_mu - ret_mu
            recs.append(r)
    # reference fetch for H2/H3
    fr, br = g.h.snap_at(fd - P["ref_min_gap_s"])
    if fr is not None and fd - fr <= P["ref_max_gap_s"] and pro_mu is not None:
        vr = view(br)
        if vr.get("pro_basis") == vd.get("pro_basis") and "retail_mu" in vr and ret_mu is not None:
            dR, dP = ret_mu - vr["retail_mu"], pro_mu - vr["pro_mu"]
            if abs(dR) >= P["H2_retail_move_pts"] and dP * math.copysign(1, dR) <= P["H2_pro_hold_pts"]:
                r = bet_record(g, "H2", b if dR > 0 else a, bd, pro_mu, close_mu, cons_d, finals, pro_close=vc.get('pro_mu'))
                if r:
                    r.update(dR=dR, dP=dP)
                    recs.append(r)
        for bk in PRO_BOOKS[vd["pro_basis"]]:
            q0, q1 = br.get(bk), bd.get(bk)
            if q0 and q1:
                if q0["point"] == q1["point"]:
                    p0, p1 = devig_a(q0), devig_a(q1)
                    for hyp, thr in (("H3", P["H3_juice_shift_p"]), ("X3_h3_at_0.015", 0.015)):
                        if p0 is not None and p1 is not None and abs(p1 - p0) >= thr:
                            r = bet_record(g, hyp, a if p1 > p0 else b, bd, pro_mu, close_mu, cons_d, finals,
                                           pro_close=vc.get('pro_mu'))
                            if r:
                                r["shift"] = p1 - p0
                                recs.append(r)
                break
    # H5a / H5b line shopping vs the pro fair (pro operator's own books excluded)
    for hyp, t_off, age in (("H5a", P["decision_offset_s"], P["decision_max_age_s"]),
                            ("H5b", P["early_offset_s"], P["early_max_age_s"])):
        f5, b5, v5 = (fd, bd, vd) if hyp == "H5a" else g.at(ko - t_off, age)
        if f5 is None or "pro_mu" not in v5:
            continue
        excl = tuple(PRO_BOOKS[v5["pro_basis"]])
        cands = [best(g.nz, v5["pro_mu"], b5, g.market, s, excl) + (s,) for s in (a, b)
                 if best(g.nz, v5["pro_mu"], b5, g.market, s, excl)]
        if cands:
            e, bk, pt, pr, s = max(cands)
            if e >= P["H5_min_ev"]:
                r = bet_record(g, hyp, s, {bk: b5[bk]}, v5["pro_mu"], close_mu, v5.get("consensus_mu"), finals, pro_close=vc.get('pro_mu'))
                if r:
                    o = best(g.nz, v5["pro_mu"], b5, g.market, OPP[s], excl)
                    r["dir_clv"] = r["clv"] - ev_at(g.nz, close_mu, OPP[s], o[2], o[3]) if o else None
                    r["ev_dec"] = e
                    recs.append(r)
    fin = finals.get((g.sport, g.eid))
    info = None
    if fin and None not in fin:
        actual = (fin[0] - fin[1]) if g.market == "spread" else (fin[0] + fin[1])
        info = {"sport": g.sport, "market": g.market, "event_id": g.eid, "actual": actual, "era": era,
                "dec_pro": pro_mu, "dec_ret": ret_mu, "dec_cons": cons_d, "close_pro": vc.get("pro_mu"),
                "close_ret": vc.get("retail_mu"), "close_cons": close_mu}
    cov["info"] = info
    for r in recs:
        r.update(era=era, kickoff=from_epoch(ko), dec_at=from_epoch(fd), close_at=from_epoch(fc))
    # H4 consecutive-fetch pairs
    pairs = []
    prev = None
    for t, bks in g.h.snaps:
        v = view(bks)
        cur = (t, v.get("pro_mu"), v.get("retail_mu"), v.get("pro_basis")) if "pro_mu" in v and "retail_mu" in v else None
        if prev and cur and cur[3] == prev[3] and cur[0] - prev[0] <= 6 * 3600:
            pairs.append({"sport": g.sport, "market": g.market, "event_id": g.eid, "gap": prev[1] - prev[2],
                          "d_ret": cur[2] - prev[2], "d_pro": cur[1] - prev[1], "hours_to_ko": (ko - prev[0]) / 3600})
        prev = cur
    return recs, pairs, cov


# ---------------------------------------------------------------------------------------------- statistics
def boot_mean(rows, key, B, rng):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return None
    n = len(vals)
    m = sum(vals) / n
    bs = sorted(sum(vals[rng.randrange(n)] for _ in range(n)) / n for _ in range(B))
    p = (sum(1 for x in bs if x <= 0) + 1) / (B + 1)
    return {"n": n, "mean": m, "lo": bs[int(.025 * B)], "hi": bs[int(.975 * B) - 1], "p_le0": p}


def slope_boot(pairs, y, B, rng):
    by = defaultdict(list)
    for p in pairs:
        by[p["event_id"]].append(p)
    keys = list(by)

    def fit(sample):
        xs = [p["gap"] for k in sample for p in by[k]]
        ys = [p[y] for k in sample for p in by[k]]
        sxx = sum(x * x for x in xs)
        return sum(x * v for x, v in zip(xs, ys)) / sxx if sxx else 0.0
    est = fit(keys)
    bs = sorted(fit([keys[rng.randrange(len(keys))] for _ in keys]) for _ in range(min(B, 1000)))
    return {"games": len(keys), "pairs": len(pairs), "slope": est, "lo": bs[int(.025 * len(bs))],
            "hi": bs[int(.975 * len(bs)) - 1]}


def holm(pvals, alpha=0.05):
    order = sorted(pvals, key=lambda k: pvals[k])
    m = len(order)
    out, stop = {}, False
    for i, k in enumerate(order):
        thr = alpha / (m - i)
        if stop or pvals[k] > thr:
            stop = True
            out[k] = False
        else:
            out[k] = True
    return out


def summarize(recs, B, rng):
    out = {}
    w = sum(1 for r in recs if r.get("profit", 0) > 0)
    l = sum(1 for r in recs if r.get("profit") == -1.0)
    pu = sum(1 for r in recs if r.get("profit") == 0.0)
    out["n"] = len(recs)
    out["record"] = f"{w}-{l}-{pu}"
    out["graded"] = w + l + pu
    out["clv"] = boot_mean(recs, "clv", B, rng)
    out["dir_clv"] = boot_mean(recs, "dir_clv", B, rng)
    out["move"] = boot_mean(recs, "move", B, rng)
    out["roi"] = boot_mean(recs, "profit", B, rng)
    ex = [dict(x=r["profit"] - r["clv"]) for r in recs if r.get("profit") is not None]
    out["outcome_excess"] = boot_mean(ex, "x", B, rng)
    out["clv_pro"] = boot_mean(recs, "clv_pro", B, rng)
    ex2 = [dict(x=r["profit"] - r["clv_pro"]) for r in recs if r.get("profit") is not None and r.get("clv_pro") is not None]
    out["outcome_excess_vs_pro_close"] = boot_mean(ex2, "x", B, rng)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    for k in ("market-db", "rankings", "results", "out"):
        ap.add_argument("--" + k, required=True)
    a = ap.parse_args(argv)
    from intel_v7.evaluate import load_finals
    conn = sqlite3.connect(f"file:{a.market_db}?mode=ro", uri=True)
    end = conn.execute("SELECT MAX(fetched_at) FROM mkt_quote").fetchone()[0]
    live_start = {s: conn.execute("SELECT MIN(fetched_at) FROM mkt_fetch WHERE endpoint='odds' AND sport=?",
                                  [k]).fetchone()[0] for s, k in (("nfl", "americanfootball_nfl"),
                                                                  ("cfb", "americanfootball_ncaaf"))}
    gl = [(s, e, k, h, aw) for s in ("nfl", "cfb") for e, k, h, aw in games(conn, s, end=end)]
    finals = load_finals(a.rankings, a.results, gl)
    recs, pairs, covs = [], [], []
    for s, e, k, h, aw in gl:
        if k > end:
            continue
        for m in ("spread", "total"):
            r, p, c = evaluate_game(Game(conn, s, e, m, live_start[s]), finals)
            recs += r
            pairs += p
            c.update(sport=s, market=m, event_id=e, kickoff=from_epoch(k))
            covs.append(c)
    rng = random.Random(PLAN["seed"])
    B = PLAN["bootstrap"]
    cells, pv = {}, {}
    for hyp in ("H1", "H2", "H3", "H5a", "H5b"):
        for s in ("nfl", "cfb"):
            for m in ("spread", "total"):
                rr = [r for r in recs if r["hyp"] == hyp and r["sport"] == s and r["market"] == m]
                key = f"{hyp} {s} {m}"
                cells[key] = summarize(rr, B, rng)
                cells[key]["by_era"] = {era: summarize([r for r in rr if r["era"] == era], 1000, rng)
                                        for era in ("archive", "live")}
                if len(rr) >= PLAN["min_cell_n"] and cells[key]["clv"]:
                    pv[key] = cells[key]["clv"]["p_le0"]
                else:
                    cells[key]["status"] = "INSUFFICIENT (n < %d)" % PLAN["min_cell_n"]
        cells[f"{hyp} all"] = summarize([r for r in recs if r["hyp"] == hyp], B, rng)
    for s_ in ("nfl", "cfb"):
        for m in ("spread", "total"):
            rr = [r for r in recs if r["hyp"] == "X3_h3_at_0.015" and r["sport"] == s_ and r["market"] == m]
            cells[f"X3_h3_at_0.015 {s_} {m} (EXPLORATORY)"] = summarize(rr, B, rng)
    passed = holm(pv)
    for k, v in passed.items():
        cells[k]["holm_pass"] = v
    h4 = {}
    for s in ("nfl", "cfb"):
        for m in ("spread", "total"):
            for win, cond in (("all", lambda p: True), ("<6h", lambda p: p["hours_to_ko"] < 6),
                              ("6-48h", lambda p: 6 <= p["hours_to_ko"] < 48)):
                pp = [p for p in pairs if p["sport"] == s and p["market"] == m and cond(p)]
                if len(pp) >= 30:
                    h4[f"{s} {m} {win}"] = {"retail_toward_pro": slope_boot(pp, "d_ret", B, rng),
                                            "pro_toward_retail": {k: (-v if k in ("slope",) else v) for k, v in
                                                                  slope_boot(pp, "d_pro", B, rng).items()}}
                    d = h4[f"{s} {m} {win}"]["pro_toward_retail"]
                    d["lo"], d["hi"] = -d["hi"], -d["lo"]
    e1 = {}
    infos = [c["info"] for c in covs if c.get("info")]
    for s_ in ("nfl", "cfb"):
        for m in ("spread", "total"):
            ii = [i for i in infos if i["sport"] == s_ and i["market"] == m]
            for when in ("dec", "close"):
                rows = [dict(event_id=i["event_id"], gap=i[when + "_pro"] - i[when + "_ret"],
                             res=i["actual"] - i[when + "_ret"],
                             d_sq=(i["actual"] - i[when + "_pro"]) ** 2 - (i["actual"] - i[when + "_ret"]) ** 2)
                        for i in ii if i[when + "_pro"] is not None and i[when + "_ret"] is not None]
                if len(rows) >= 30:
                    e1[f"{s_} {m} {when}"] = {"slope_outcome_on_gap": slope_boot(rows, "res", B, rng),
                                              "mse_pro_minus_retail": boot_mean(rows, "d_sq", B, rng),
                                              "big_gap_n": sum(1 for r in rows if abs(r["gap"]) >= 0.5)}
    os.makedirs(a.out, exist_ok=True)
    json.dump({"plan_hash": PLAN_HASH, "data_end": from_epoch(end), "finals": len(finals), "cells": cells,
               "h4": h4, "e1_information": e1, "coverage": covs}, open(os.path.join(a.out, "results.json"), "w"), default=str, indent=1)
    with open(os.path.join(a.out, "bets.jsonl"), "w") as f:
        for r in recs:
            f.write(json.dumps(r, default=str) + "\n")
    print(len(recs), "bets;", len(pairs), "pairs;", len(finals), "finals")


if __name__ == "__main__":
    main()
