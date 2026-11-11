"""
research_v4/tier_a.py -- Tier A studies A2-A7 on the normalized panel. RESEARCH ONLY, development data.

Every behaviour record separates: OBSERVED action -> implied PRICING OPINION -> CANDIDATE preferred-NEED side
-> IDENTIFIABLE? (else UNKNOWN). Nothing is labelled sportsbook liability.

  python -m research_v4.tier_a --panel panel.json.gz --out tier_a.json
"""

import argparse
import collections
import gzip
import json
import math
import random
import statistics as st

from research_v4 import normalize as NZ
from research_v4.panel import MAKERS, OWNER, REC, SIDES

MOVE = 0.5           # points of implied fair line (mu) that count as a move
GAP = 0.5            # points of mu that count as a disagreement / resistance
CLOSE_MAX_S = 2 * 3600
PICK_WINDOW = (45 * 60, 90 * 60)   # decision = first fetch 90..45 min before kickoff


def load(path):
    d = json.load(gzip.open(path, "rt"))
    by = collections.defaultdict(lambda: collections.defaultdict(dict))      # (e,m) -> t -> book -> row
    for r in d["rows"]:
        by[(r["e"], r["m"])][r["t"]][r["b"]] = r
    return by, d["events"], d["info"]


def norms(info):
    import numpy as np  # noqa: F401
    nm, nt = NZ.nfl()
    return nm, nt


def side_names(m):
    return SIDES[m]


def med(xs):
    return st.median(xs) if xs else None


def ref_mu(snap, exclude=None, books=None):
    xs = [r["mu"] for b, r in snap.items() if b != exclude and (books is None or b in books)]
    return med(xs)


def mu_pin(snap):
    return snap["pinnacle"]["mu"] if "pinnacle" in snap else None


def favours(m, d_mu):
    """side whose chances a HIGHER mu improves: home (spreads: mu = home margin) / over (totals)."""
    a, b = side_names(m)
    return a if d_mu > 0 else b


# ---------------------------------------------------------------- A2 audit
def a2(by):
    pres = collections.Counter(); possible = 0
    stale_runs = collections.Counter(); active_moves = collections.Counter()
    for (e, m), ts in by.items():
        times = sorted(ts)
        for t in times:
            possible += 1
            for b in ts[t]:
                pres[b] += 1
        # staleness: book identical (pt, prices) across consecutive fetches while the other books' median mu moved >= MOVE
        for b in set(x for t in times for x in ts[t]):
            seq = [(t, ts[t][b]) for t in times if b in ts[t]]
            for (t0, r0), (t1, r1) in zip(seq, seq[1:]):
                o0, o1 = ref_mu(ts[t0], exclude=b), ref_mu(ts[t1], exclude=b)
                if o0 is None or o1 is None or abs(o1 - o0) < MOVE:
                    continue
                same = (r0["pt"], r0["pa"], r0["pb"]) == (r1["pt"], r1["pa"], r1["pb"])
                (stale_runs if same else active_moves)[b] += 1
    return {"coverage": {b: round(n / possible, 3) for b, n in pres.most_common()},
            "unchanged_while_market_moved": {b: round(stale_runs[b] / max(1, stale_runs[b] + active_moves[b]), 3)
                                             for b in pres},
            "event_fetches": possible}


# ---------------------------------------------------------------- A3 leadership
def a3(by, ev):
    out = collections.Counter(); follow = collections.defaultdict(lambda: [0, 0]); recs = []
    for (e, m), ts in by.items():
        times = sorted(t for t in ts if t < ev[e]["kickoff"])
        if len(times) < 3:
            continue
        books = set(b for t in times for b in ts[t])
        for d in (1, -1):
            first = {}
            for b in books:
                seq = [(t, ts[t][b]["mu"]) for t in times if b in ts[t]]
                for (t0, x0), (t1, x1) in zip(seq, seq[1:]):
                    if (x1 - x0) * d >= MOVE:
                        first[b] = t1
                        break
            if len(first) < 3:
                continue
            t0 = min(first.values())
            lead = sorted(b for b, t in first.items() if t == t0)
            kind = "TIED" if len(lead) > 1 else lead[0]
            out[kind if kind == "TIED" else ("pinnacle" if kind == "pinnacle" else
                                              "maker(BOL group)" if kind in MAKERS else "recreational")] += 1
            if kind == "pinnacle" or (kind != "TIED" and kind in REC):
                grp = "pinnacle-led" if kind == "pinnacle" else "rec-led"
                followers = [b for b in first if b != kind and first[b] > t0]
                follow[grp][0] += 1
                follow[grp][1] += len(followers) / max(1, len(books) - 1)
            recs.append({"e": e, "m": m, "dir": d, "leader": kind, "t": t0, "n_moved": len(first)})
    return {"episodes": sum(out.values()), "first_observed": dict(out),
            "follow_share": {k: round(v[1] / v[0], 3) for k, v in follow.items()},
            "records": recs}


# ---------------------------------------------------------------- A4 resistance (+ directional record)
def a4(by, ev):
    events = []
    for (e, m), ts in by.items():
        times = sorted(t for t in ts if t < ev[e]["kickoff"])
        for b in set(x for t in times for x in ts[t]):
            run = []
            for t in times:
                if b not in ts[t]:
                    run = []
                    continue
                ref = ref_mu(ts[t], exclude=b)
                if ref is None:
                    run = []
                    continue
                dev = ts[t][b]["mu"] - ref
                if abs(dev) >= GAP and (not run or math.copysign(1, dev) == math.copysign(1, run[-1][1])):
                    run.append((t, dev))
                else:
                    if len(run) >= 2:
                        events.append(_res_event(e, m, b, run, ts, ev))
                    run = [(t, dev)] if abs(dev) >= GAP else []
            if len(run) >= 2:
                events.append(_res_event(e, m, b, run, ts, ev))
    return events


def _res_event(e, m, b, run, ts, ev):
    t_first, t_last = run[0][0], run[-1][0]
    rows = [ts[t][b] for t, _ in run]
    changed = len({(r["pt"], r["pa"], r["pb"]) for r in rows}) > 1
    point_held = len({r["pt"] for r in rows}) == 1
    on_key = m == "spreads" and abs(rows[0]["pt"]) in (3, 7)
    dev = run[0][1]
    # OBSERVED: book b priced mu `dev` away from everyone else for len(run) fetches
    observed = ("held point, moved juice" if point_held and changed else
                "moved its number" if changed else "quote unchanged")
    # PRICING OPINION: b rates the higher-mu side (home/over) stronger than the market if dev > 0
    opinion_side = favours(m, dev)
    # NEED: a pricing deviation -- even an active, deliberate one -- does NOT identify the outcome the book wants to
    # need (owner rule, 2026-11-11). candidate_need_side is UNKNOWN unless independent evidence of the book's
    # financial position exists, and no such evidence exists in odds data. `identifiable` now means only that the
    # PRICING-OPINION direction is readable (active market-maker deviation), not that a NEED is known.
    active = changed
    identifiable = active and b in MAKERS
    hyp = ("H5 stale-consistent" if not active else
           "H2/H4-consistent (informed or deliberate)" if b in MAKERS else "recreational deviation (H1/H5)")
    return {"e": e, "m": m, "book": b, "t_first": t_first, "t_last": t_last, "fetches": len(run),
            "dev_mu": round(dev, 2), "observed": observed, "on_key": on_key,
            "pricing_opinion_side": opinion_side if identifiable else "UNKNOWN",
            "candidate_need_side": "UNKNOWN", "opinion_readable": identifiable, "identifiable": identifiable,
            "hypothesis": hyp}


# ---------------------------------------------------------------- shared: decision & close helpers
def decision_time(ts, kick):
    c = [t for t in sorted(ts) if PICK_WINDOW[0] <= kick - t <= PICK_WINDOW[1]]
    return c[0] if c else None


def close_time(ts, kick):
    c = [t for t in sorted(ts) if t < kick]
    return c[-1] if c and kick - c[-1] <= CLOSE_MAX_S else None


def maker_mu(snap):
    xs = [snap[b]["mu"] for b in MAKERS if b in snap]
    if "pinnacle" in snap:
        return snap["pinnacle"]["mu"]
    return med(xs)


def best_owner(snap, m, side, nz):
    """Best executable quote for `side` among OWNER books: (book, point, price, p_win, p_push) at that book's own
    number, with probability from the NO-VIG Pinnacle/maker mu at the same fetch (fair value at decision time)."""
    best = None
    fair = maker_mu(snap)
    if fair is None:
        return None
    a, b_ = SIDES[m]
    for bk in OWNER:
        r = snap.get(bk)
        if not r:
            continue
        pt = r["pt"] if side == a else (-r["pt"] if m == "spreads" else r["pt"])
        price = r["pa"] if side == a else r["pb"]
        pw, pp = nz.prob_at(fair, side, pt)
        dec = 1 + (100 / abs(price) if price < 0 else price / 100)
        ev = pw * (dec - 1) - (1 - pw - pp)
        if best is None or ev > best["ev"]:
            best = {"book": bk, "point": pt, "price": price, "p_win": pw, "p_push": pp, "ev": ev}
    return best


def grade(m, side, point, final):
    h, a = final
    if m == "spreads":
        v = (h - a + point) if side == "home" else (a - h + point)
    else:
        v = (h + a - point) if side == "over" else (point - h - a)
    return "W" if v > 0 else "L" if v < 0 else "P"


def settle(price, res):
    if res == "P":
        return 0.0
    win = 100 / abs(price) if price < 0 else price / 100
    return win if res == "W" else -1.0


# ---------------------------------------------------------------- A5 disagreement -> movement
def a5(by, ev):
    pts = collections.defaultdict(list)
    for (e, m), ts in by.items():
        kick = ev[e]["kickoff"]; ct = close_time(ts, kick)
        if ct is None:
            continue
        cs = ts[ct]
        for lab, lo, hi in (("T-24h", 18 * 3600, 30 * 3600), ("T-6h", 4 * 3600, 8 * 3600), ("T-1h", *PICK_WINDOW)):
            c = [t for t in sorted(ts) if lo <= kick - t <= hi and t < ct]
            if not c:
                continue
            s = ts[c[0]]
            p, rm = mu_pin(s), ref_mu(s, books=REC)
            pc, rmc = mu_pin(cs), ref_mu(cs, books=REC)
            if None in (p, rm, pc, rmc):
                continue
            pts[(ev[e]["sport"], m, lab)].append((p - rm, rmc - rm, pc - p))
    out = {}
    for k, v in pts.items():
        g = [x[0] for x in v]
        out["|".join(k)] = {"n": len(v), "rec_toward_pin_slope": _slope(g, [x[1] for x in v]),
                            "pin_toward_rec_slope": _slope([-x for x in g], [x[2] for x in v]),
                            "share_gap_ge_0.5": round(sum(abs(x) >= GAP for x in g) / len(g), 3)}
    return out


def _slope(x, y, boots=400):
    def fit(ix):
        mx = st.mean(x[i] for i in ix); my = st.mean(y[i] for i in ix)
        sxx = sum((x[i] - mx) ** 2 for i in ix)
        return sum((x[i] - mx) * (y[i] - my) for i in ix) / sxx if sxx else float("nan")
    n = len(x)
    if n < 10:
        return None
    b = fit(range(n)); rnd = random.Random(3)
    bs = sorted(fit([rnd.randrange(n) for _ in range(n)]) for _ in range(boots))
    return {"slope": round(b, 3), "ci95": [round(bs[int(.025 * boots)], 3), round(bs[int(.975 * boots)], 3)]}


# ---------------------------------------------------------------- A6 executable EV / CLV / outcomes
def a6(by, ev, nzs, min_ev=(0.0, 0.01, 0.02)):
    res = collections.defaultdict(list)
    for (e, m), ts in by.items():
        v = ev[e]; kick = v["kickoff"]; nz = nzs[(v["sport"], m)]
        dt, ct = decision_time(ts, kick), close_time(ts, kick)
        if dt is None or ct is None or dt >= ct:
            continue
        snap = ts[dt]
        cands = [best_owner(snap, m, s, nz) for s in SIDES[m]]
        cands = [(s, c) for s, c in zip(SIDES[m], cands) if c]
        if not cands:
            continue
        s, c = max(cands, key=lambda x: x[1]["ev"])
        fair_close = maker_mu(ts[ct])
        if fair_close is None:
            continue
        pw, pp = nz.prob_at(fair_close, s, c["point"])
        dec = 1 + (100 / abs(c["price"]) if c["price"] < 0 else c["price"] / 100)
        clv_ev = pw * (dec - 1) - (1 - pw - pp)
        rec = {"e": e, "sport": v["sport"], "m": m, "side": s, "book": c["book"], "point": c["point"],
               "price": c["price"], "ev_at_decision": c["ev"], "ev_at_close": clv_ev,
               "quote_age_min": None}
        if v["final"]:
            r = grade(m, s, c["point"], v["final"]); rec["res"] = r; rec["pnl"] = settle(c["price"], r)
        for th in min_ev:
            if c["ev"] >= th:
                res[(v["sport"], m, th)].append(rec)
    out = {}
    for k, rs in res.items():
        g = [r for r in rs if "res" in r]
        w = sum(r["res"] == "W" for r in g); l_ = sum(r["res"] == "L" for r in g)
        risk = sum(1 for r in g if r["res"] != "P")
        out["%s|%s|EV>=%.0f%%" % (k[0], k[1], 100 * k[2])] = {
            "n": len(rs), "mean_ev_decision": round(st.mean(r["ev_at_decision"] for r in rs), 4),
            "mean_ev_at_close(CLV)": round(st.mean(r["ev_at_close"] for r in rs), 4),
            "share_clv_positive": round(sum(r["ev_at_close"] > 0 for r in rs) / len(rs), 3),
            "graded": len(g), "record": "%d-%d-%d" % (w, l_, len(g) - w - l_),
            "roi_actual_odds": round(sum(r["pnl"] for r in g) / risk, 4) if risk else None}
    return out, res


# ---------------------------------------------------------------- A7 outcome calibration
def a7(by, ev, nzs):
    srcs = ("pin_open", "pin_T-24h", "pin_T-1h", "makers_T-1h", "rec_T-1h", "pin_close", "rec_close")
    rows = []
    for (e, m), ts in by.items():
        v = ev[e]
        if not v["final"]:
            continue
        kick = v["kickoff"]; nz = nzs[(v["sport"], m)]
        ct, dt = close_time(ts, kick), decision_time(ts, kick)
        if ct is None or dt is None or "pinnacle" not in ts[ct]:
            continue
        ref_pt = ts[ct]["pinnacle"]["pt"]            # common reference number: Pinnacle's closing main line
        a = SIDES[m][0]
        res = grade(m, a, ref_pt, v["final"])
        if res == "P":
            continue
        times = sorted(t for t in ts if t < kick)
        t24 = [t for t in times if 18 * 3600 <= kick - t <= 30 * 3600]
        mus = {"pin_open": next((ts[t]["pinnacle"]["mu"] for t in times if "pinnacle" in ts[t]), None),
               "pin_T-24h": ts[t24[0]]["pinnacle"]["mu"] if t24 and "pinnacle" in ts[t24[0]] else None,
               "pin_T-1h": mu_pin(ts[dt]), "makers_T-1h": maker_mu(ts[dt]), "rec_T-1h": ref_mu(ts[dt], books=REC),
               "pin_close": mu_pin(ts[ct]), "rec_close": ref_mu(ts[ct], books=REC)}
        if any(mus[k] is None for k in ("pin_T-1h", "rec_T-1h", "pin_close", "rec_close")):
            continue
        p = {}
        for k, mu in mus.items():
            if mu is None:
                continue
            pw, pp = nz.prob_at(mu, a, ref_pt)
            p[k] = min(max(pw / (1 - pp), 1e-4), 1 - 1e-4)
        rows.append({"sport": v["sport"], "m": m, "y": 1 if res == "W" else 0, "p": p})
    out = {}
    for sp in ("nfl", "cfb"):
        for m in ("spreads", "totals"):
            rs = [r for r in rows if r["sport"] == sp and r["m"] == m]
            if not rs:
                continue
            blk = {"n": len(rs)}
            for s in srcs:
                rr = [r for r in rs if s in r["p"]]
                if len(rr) < 20:
                    continue
                ll = [-(r["y"] * math.log(r["p"][s]) + (1 - r["y"]) * math.log(1 - r["p"][s])) for r in rr]
                br = [(r["p"][s] - r["y"]) ** 2 for r in rr]
                blk[s] = {"n": len(rr), "logloss": round(st.mean(ll), 4), "brier": round(st.mean(br), 4)}
            # paired difference vs pin_close (bootstrap over games)
            for s in ("pin_T-1h", "rec_T-1h", "rec_close"):
                d = [(-(r["y"] * math.log(r["p"][s]) + (1 - r["y"]) * math.log(1 - r["p"][s])))
                     - (-(r["y"] * math.log(r["p"]["pin_close"]) + (1 - r["y"]) * math.log(1 - r["p"]["pin_close"])))
                     for r in rs if s in r["p"]]
                rnd = random.Random(5); n = len(d)
                bs = sorted(st.mean(d[rnd.randrange(n)] for _ in range(n)) for _ in range(400))
                blk["diff_vs_pin_close:" + s] = {"mean": round(st.mean(d), 4), "ci95": [round(bs[10], 4), round(bs[389], 4)]}
            out[sp + "|" + m] = blk
    return out


def run(panel_path):
    by, ev, info = load(panel_path)
    nm, nt = NZ.nfl()
    cw = {int(k): v for k, v in info["cfb_margin_weights"].items()}
    tw = {int(k): v for k, v in info["cfb_total_weights"].items()}
    cfb_m = NZ.Normalizer("margin", info["cfb_margin_sigma"], cw, NZ.D.M_RANGE, "LOW CONFIDENCE (CFB 2026 only)")
    cfb_t = NZ.Normalizer("total", info["cfb_total_sigma"], tw, NZ.D.T_RANGE, "LOW CONFIDENCE (CFB 2026 only)")
    nzs = {("nfl", "spreads"): nm, ("nfl", "totals"): nt, ("cfb", "spreads"): cfb_m, ("cfb", "totals"): cfb_t}
    out = {"info": info, "A2": a2(by)}
    l3 = a3(by, ev); out["A3"] = {k: v for k, v in l3.items() if k != "records"}
    res = a4(by, ev)
    out["A4"] = {"events": len(res), "by_book": dict(collections.Counter(r["book"] for r in res)),
                 "by_hypothesis": dict(collections.Counter(r["hypothesis"] for r in res)),
                 "identifiable_share": round(sum(r["identifiable"] for r in res) / max(1, len(res)), 3),
                 "observed": dict(collections.Counter(r["observed"] for r in res))}
    out["A5"] = a5(by, ev)
    out["A6"], a6rows = a6(by, ev, nzs)
    out["A7"] = a7(by, ev, nzs)
    return out, res, a6rows, by, ev, nzs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out, res, *_ = run(a.panel)
    json.dump({"summary": out, "resistance_events": res}, open(a.out, "w"), default=str)
    print(json.dumps(out, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
