"""
lab_v4/snapshot_trends.py -- simple, pre-declared trends from the collected odds snapshots vs actual results.
EXPLORATORY (2026 already studied). Every bet: best REAL price among fresh books at the latest snapshot <= kickoff - 1 h
(<= 3 h old). Closing value vs the all-book consensus close. One bet per game-market; CIs bootstrap games; Holm.

  python -m lab_v4.snapshot_trends --market-db market.db --rankings DIR --results DIR
"""
import argparse
import collections
import random
import sqlite3
import statistics as st

from intel_v7.evaluate import load_finals
from intel_v7.history import History, games, view
from need_v6.market import normalizer
from sportsbook_v4.market import profit_on_win

SIDES = {"spread": ("home", "away"), "total": ("over", "under")}
OPP = {"home": "away", "away": "home", "over": "under", "under": "over"}
SIGN = {"home": 1, "over": 1, "away": -1, "under": -1}
RULES = ["T1 favourite (closing line)", "T1b underdog (closing line)", "T2 home", "T2b away", "T3 over", "T3b under",
         "T4 big favourite (NFL >=7, CFB >=14)", "T4b big underdog", "M1 follow 1+ pt move (first snapshot -> T-1h)",
         "M2 fade 1+ pt move", "M3 follow late 0.5+ pt move (T-6h -> T-1h)", "M4 fade late 0.5+ pt move",
         "M5 NFL spread moved through 3 or 7: follow", "M6 NFL spread moved through 3 or 7: fade"]


def side_quotes(books, m, side):
    a, _ = SIDES[m]
    for bk, q in books.items():
        if q["mu"] is None:
            continue
        if side == a:
            yield bk, q["point"], q["price_a"]
        else:
            yield bk, (-q["point"] if m == "spread" else q["point"]), q["price_b"]


def ev(nz, mu, side, pt, pr):
    pw, pp = nz.prob_at(mu, side, pt)
    return pw * profit_on_win(1.0, pr) - (1 - pw - pp)


def collect(conn, finals):
    out = []
    for sport in ("nfl", "cfb"):
        for eid, ko, h, a in games(conn, sport):
            fin = finals.get((sport, eid))
            if not fin or None in fin:
                continue
            for m in ("spread", "total"):
                hist = History(conn, sport, eid, m)
                if not hist.snaps:
                    continue
                ko = hist.kickoff
                fd, bd = hist.snap_at(ko - 3600)
                fc, bc = hist.snap_at(ko - 1)
                if fd is None or ko - 3600 - fd > 3 * 3600 or fc is None or ko - fc > 5400:
                    continue
                vd, vc = view(bd), view(bc)
                f0, b0 = hist.snaps[0]
                v0 = view(b0)
                f6, b6 = hist.snap_at(ko - 6 * 3600)
                v6 = view(b6) if f6 is not None and f6 < fd else {}
                if None in (vd.get("consensus_mu"), vc.get("consensus_mu"), v0.get("consensus_mu")):
                    continue
                nz = normalizer(sport, m)
                pts = sorted(q["point"] for q in bd.values() if q["point"] is not None)
                rec = {"sport": sport, "event_id": eid, "market": m, "mu0": v0["consensus_mu"], "mu6": v6.get("consensus_mu"),
                       "mud": vd["consensus_mu"], "muc": vc["consensus_mu"], "line_d": pts[len(pts) // 2],
                       "line_0": sorted(q["point"] for q in b0.values() if q["point"] is not None)[0], "bets": {}}
                pts0 = sorted(q["point"] for q in b0.values() if q["point"] is not None)
                rec["line_0"] = pts0[len(pts0) // 2]
                ptsc = sorted(q["point"] for q in bc.values() if q["point"] is not None)
                rec["line_c"] = ptsc[len(ptsc) // 2]
                hs, as_ = fin
                for s in SIDES[m]:
                    best = max(((ev(nz, vd["consensus_mu"], s, pt, pr), bk, pt, pr) for bk, pt, pr in side_quotes(bd, m, s)),
                               default=None)
                    if best is None:
                        continue
                    _, bk, pt, pr = best
                    v = ((hs - as_) * SIGN[s] + pt) if m == "spread" else ((hs + as_ - pt) * SIGN[s])
                    units = profit_on_win(1.0, pr) if v > 0 else (0.0 if v == 0 else -1.0)
                    rec["bets"][s] = {"units": units, "res": "W" if v > 0 else "P" if v == 0 else "L",
                                      "clv": ev(nz, vc["consensus_mu"], s, pt, pr)}
                if len(rec["bets"]) == 2:
                    out.append(rec)
    return out


def pick(rule, r):
    s, m = r["sport"], r["market"]
    if rule.startswith(("T1", "T4")):
        if m != "spread" or r["line_c"] == 0:
            return None
        fav = "home" if r["line_c"] < 0 else "away"
        if rule.startswith("T4"):
            if abs(r["line_c"]) < (7 if s == "nfl" else 14):
                return None
        return fav if not rule.split()[0].endswith("b") else OPP[fav]
    if rule.startswith("T2"):
        return None if m != "spread" else ("home" if rule.startswith("T2 ") else "away")
    if rule.startswith("T3"):
        return None if m != "total" else ("over" if rule.startswith("T3 ") else "under")
    a, b = SIDES[m]
    if rule.startswith(("M1", "M2")):
        d = r["mud"] - r["mu0"]
        if abs(d) < 1.0:
            return None
        s_ = a if d > 0 else b
        return s_ if rule.startswith("M1") else OPP[s_]
    if rule.startswith(("M3", "M4")):
        if r["mu6"] is None:
            return None
        d = r["mud"] - r["mu6"]
        if abs(d) < 0.5:
            return None
        s_ = a if d > 0 else b
        return s_ if rule.startswith("M3") else OPP[s_]
    if rule.startswith(("M5", "M6")):
        if s != "nfl" or m != "spread":
            return None
        l0, ld = -r["line_0"], -r["line_d"]          # expected home margin implied by the home line
        crossed = any(min(l0, ld) < k < max(l0, ld) or min(l0, ld) < -k < max(l0, ld) for k in (3, 7))
        if not crossed:
            return None
        s_ = "home" if ld > l0 else "away"
        return s_ if rule.startswith("M5") else OPP[s_]


def summarize(rows, B=2000, seed=5):
    if not rows:
        return None
    w = sum(r[1]["res"] == "W" for r in rows)
    l = sum(r[1]["res"] == "L" for r in rows)
    p = len(rows) - w - l
    u = [r[1]["units"] for r in rows]
    rng = random.Random(seed)
    bs = sorted(st.mean(rng.choice(u) for _ in u) for _ in range(B))
    return {"n": len(rows), "rec": f"{w}-{l}-{p}", "win": 100 * w / max(1, w + l), "roi": 100 * st.mean(u),
            "lo": 100 * bs[int(.025 * B)], "hi": 100 * bs[int(.975 * B) - 1], "p": (sum(x <= 0 for x in bs) + 1) / (B + 1),
            "clv": 100 * st.mean(r[1]["clv"] for r in rows)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    for k in ("market-db", "rankings", "results"):
        ap.add_argument("--" + k, required=True)
    a = ap.parse_args(argv)
    conn = sqlite3.connect(f"file:{a.market_db}?mode=ro", uri=True)
    gl = [(s, e, k, h, aw) for s in ("nfl", "cfb") for e, k, h, aw in games(conn, s)]
    finals = load_finals(a.rankings, a.results, gl)
    recs = collect(conn, finals)
    print("graded game-markets:", collections.Counter((r["sport"], r["market"]) for r in recs))
    base = summarize([(r, r["bets"][s]) for r in recs for s in SIDES[r["market"]]])
    print(f"BASELINE (every side at best T-1h price): {base['rec']} ROI {base['roi']:+.1f}% CLV {base['clv']:+.2f}%")
    pv, res = {}, {}
    for rule in RULES:
        for sp in ("nfl", "cfb", "all"):
            rows = []
            for r in recs:
                if sp != "all" and r["sport"] != sp:
                    continue
                s = pick(rule, r)
                if s:
                    rows.append((r, r["bets"][s]))
            sm = summarize(rows)
            if sm:
                res[(rule, sp)] = sm
                if sp != "all":
                    pv[(rule, sp)] = sm["p"]
    ks = sorted(pv, key=pv.get)
    holm, stop = {}, False
    for i, k in enumerate(ks):
        stop = stop or pv[k] > 0.05 / (len(ks) - i)
        holm[k] = not stop
    for (rule, sp), s in res.items():
        print(f"{rule:48s} {sp:4s} n {s['n']:4d} {s['rec']:11s} win {s['win']:5.1f}% ROI {s['roi']:+6.1f}% "
              f"[{s['lo']:+.0f},{s['hi']:+.0f}] p {s['p']:.3f} CLV {s['clv']:+.2f}% {'HOLM-PASS' if holm.get((rule, sp)) else ''}")
    print("tests:", len(pv))


if __name__ == "__main__":
    main()
