"""
original_v3/evaluate.py -- grade the ORIGINAL ArbitrEdge outputs (original_v3.replay) on 2026 games.
EXPLORATORY (2026 already studied). Point spreads only (the original Book NEED has no totals logic).
Each bet: best REAL price for the side among books quoted in the latest fetch <= the decision time (<= 3 h old);
closing value vs the all-book consensus close; one bet per game per test; game-level bootstrap; Holm across tests.

  python -m original_v3.evaluate --replay FILE --market-db DB [--v4-ledger FILE ...]
"""
import argparse
import collections
import pickle
import random
import sqlite3
import statistics as st

from intel_v7.history import History, view
from need_v6.market import normalizer
from sportsbook_v4.market import profit_on_win
from sportsbook_v4.util import to_epoch

OPP = {"home": "away", "away": "home"}


class Grader:
    def __init__(self, conn, finals):
        self.conn, self.finals, self.h = conn, finals, {}

    def hist(self, sport, eid):
        k = (sport, eid)
        if k not in self.h:
            self.h[k] = History(self.conn, sport, eid, "spread")
        return self.h[k]

    def bet(self, sport, eid, side, t):
        h = self.hist(sport, eid)
        f, b = h.snap_at(t)
        if f is None or t - f > 3 * 3600:
            return None
        nz = normalizer(sport, "spread")
        v = view(b)
        mu = v.get("consensus_mu")
        if mu is None:
            return None
        best = None
        for bk, q in b.items():
            if q["mu"] is None:
                continue
            pt, pr = (q["point"], q["price_a"]) if side == "home" else (-q["point"], q["price_b"])
            pw, pp = nz.prob_at(mu, side, pt)
            e = pw * profit_on_win(1.0, pr) - (1 - pw - pp)
            if best is None or e > best[0]:
                best = (e, pt, pr)
        if best is None:
            return None
        _, pt, pr = best
        fc, bc = h.snap_at(h.kickoff - 1)
        clv = None
        if fc is not None and h.kickoff - fc <= 5400 and view(bc).get("consensus_mu") is not None:
            pw, pp = nz.prob_at(view(bc)["consensus_mu"], side, pt)
            clv = pw * profit_on_win(1.0, pr) - (1 - pw - pp)
        fin = self.finals.get((sport, eid))
        if not fin or None in fin:
            return None
        v_ = (fin[0] - fin[1]) * (1 if side == "home" else -1) + pt
        return {"units": profit_on_win(1.0, pr) if v_ > 0 else (0.0 if v_ == 0 else -1.0),
                "res": "W" if v_ > 0 else "P" if v_ == 0 else "L", "clv": clv, "game": (sport, eid)}


def summ(rows, B=2000, seed=9):
    rows = [r for r in rows if r]
    if not rows:
        return {"n": 0}
    u = [r["units"] for r in rows]
    w = sum(r["res"] == "W" for r in rows)
    l = sum(r["res"] == "L" for r in rows)
    rng = random.Random(seed)
    bs = sorted(st.mean(rng.choice(u) for _ in u) for _ in range(B))
    c = [r["clv"] for r in rows if r["clv"] is not None]
    return {"n": len(u), "rec": f"{w}-{l}-{len(u) - w - l}", "win": 100 * w / max(1, w + l), "roi": 100 * st.mean(u),
            "lo": 100 * bs[int(.025 * B)], "hi": 100 * bs[int(.975 * B) - 1], "p": (sum(x <= 0 for x in bs) + 1) / (B + 1),
            "clv": 100 * st.mean(c) if c else None}


def holm(p):
    ks = sorted(p, key=p.get)
    out, stop = {}, False
    for i, k in enumerate(ks):
        stop = stop or p[k] > 0.05 / (len(ks) - i)
        out[k] = not stop
    return out


def tests(rep, g):
    """{name: rows} -- each original component as its own pre-specified test (no thresholds tuned here)."""
    res, meta = rep["results"], rep["meta"]
    T = collections.defaultdict(list)
    for x in res:
        if "error" in x:
            continue
        m = meta[x["gid"]]
        sp, eid, lab = m["sport"], m["event_id"], x["as_of_label"]
        t = to_epoch(x["as_of"])
        pp = x.get("public_pressure") or {}
        spd = pp.get("side_preference_direction")
        key = f"{sp} {lab}"
        if x["side"] in ("home", "away"):
            b = g.bet(sp, eid, x["side"], t)
            T[f"O1 Book NEED (all) | {key}"].append(b)
            T[f"O2 Book NEED strength {x['strength']} | {key}"].append(b)
            T[f"O3 Book NEED regime {x['regime']} | {key}"].append(b)
            if x["regime"] == "RESISTANCE" and any(v.get("key_number_state") not in (None, "NO_KEY_NUMBER_INVOLVEMENT")
                                                     for v in x["per_book"].values() if v.get("regime") != "INSUFFICIENT"):
                T[f"O4 RESISTANCE with key-number corroboration | {key}"].append(b)
            T[f"C1 OPPOSITE of Book NEED (control) | {key}"].append(g.bet(sp, eid, OPP[x["side"]], t))
        if spd in ("home", "away"):
            T[f"S1 fade Side Preference direction (any magnitude) | {key}"].append(g.bet(sp, eid, OPP[spd], t))
            mag = pp.get("side_preference_magnitude_percentile")
            if mag is not None and mag > 50 and not pp.get("side_preference_magnitude_undefined"):
                T[f"S2 fade clear Public Pressure (SP magnitude > 50th pct) | {key}"].append(g.bet(sp, eid, OPP[spd], t))
                hi = pp.get("handle_intensity_percentile")
                if hi is not None:
                    T[f"S3 fade clear Public Pressure, Handle Intensity {'> 50' if hi > 50 else '<= 50'} | {key}"].append(
                        g.bet(sp, eid, OPP[spd], t))
    return T


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", required=True)
    ap.add_argument("--market-db", required=True)
    a = ap.parse_args(argv)
    rep = pickle.load(open(a.replay, "rb"))
    g = Grader(sqlite3.connect(f"file:{a.market_db}?mode=ro", uri=True), rep["finals"])
    T = tests(rep, g)
    out = {k: summ(v) for k, v in T.items()}
    # pooled per component (NFL+CFB, both decision times share games -> pooled at T-1h only to keep one bet per game)
    pv = {k: s["p"] for k, s in out.items() if s["n"] >= 20 and "control" not in k}
    hp = holm(pv)
    for k in sorted(out):
        s = out[k]
        if not s["n"]:
            continue
        print(f"{k:85s} n {s['n']:4d} {s['rec']:10s} win {s['win']:5.1f}% ROI {s['roi']:+6.1f}% [{s['lo']:+.0f},{s['hi']:+.0f}] "
              f"p {s['p']:.3f} CLV {s['clv'] if s['clv'] is None else round(s['clv'], 2)} "
              f"{'HOLM-PASS' if hp.get(k) else ('' if k in hp else '(n<20, untested)' if 'control' not in k else '')}")
    print("tests with n>=20:", len(pv))
    pickle.dump(out, open(a.replay + ".eval.pkl", "wb"))


if __name__ == "__main__":
    main()
