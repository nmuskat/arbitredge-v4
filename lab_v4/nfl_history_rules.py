"""
lab_v4/nfl_history_rules.py -- 8 pre-declared NFL betting rules tested on nflverse closing lines 2009-2026.
Rules fixed before any result was computed (2026-11-12). Graded at the CLOSING line and the closing price for that
side (nflverse consensus), push = 0. One bet per game per rule. Regular season + playoffs.
"""
import random
import statistics as st
import sys

import pandas as pd

RULES = {
    "N1 underdog +7 or more": "spread",
    "N2 road team": "spread",
    "N3 divisional underdog": "spread",
    "N4 team with 3+ more rest days": "spread",
    "N5 home vs road team on short rest (<=5 days)": "spread",
    "N6 under when wind >= 15 mph (outdoors)": "total",
    "N7 under when total >= 50": "total",
    "N8 home underdog": "spread",
}


def payout(p):
    return p / 100 if p > 0 else 100 / -p


def bets(g):
    out = []
    for r in g.itertuples():
        hl = -r.spread_line                      # home line (negative = home favoured)
        margin = r.home_score - r.away_score
        tot = r.home_score + r.away_score

        def sp(side):
            line = hl if side == "home" else -hl
            v = (margin if side == "home" else -margin) + line
            pr = r.home_spread_odds if side == "home" else r.away_spread_odds
            return v, pr

        def tt(side):
            v = (tot - r.total_line) * (1 if side == "over" else -1)
            return v, (r.over_odds if side == "over" else r.under_odds)
        dog = "home" if hl > 0 else ("away" if hl < 0 else None)
        picks = {}
        if dog and abs(hl) >= 7:
            picks["N1 underdog +7 or more"] = sp(dog)
        if r.location != "Neutral":
            picks["N2 road team"] = sp("away")
        if dog and r.div_game == 1:
            picks["N3 divisional underdog"] = sp(dog)
        if pd.notna(r.home_rest) and pd.notna(r.away_rest) and abs(r.home_rest - r.away_rest) >= 3:
            picks["N4 team with 3+ more rest days"] = sp("home" if r.home_rest > r.away_rest else "away")
        if pd.notna(r.away_rest) and r.away_rest <= 5 and r.home_rest > 5 and r.location != "Neutral":
            picks["N5 home vs road team on short rest (<=5 days)"] = sp("home")
        if pd.notna(r.wind) and r.wind >= 15 and r.roof in ("outdoors", "open"):
            picks["N6 under when wind >= 15 mph (outdoors)"] = tt("under")
        if r.total_line >= 50:
            picks["N7 under when total >= 50"] = tt("under")
        if dog == "home" and r.location != "Neutral":
            picks["N8 home underdog"] = sp("home")
        for k, (v, pr) in picks.items():
            if pd.isna(pr):
                continue
            u = payout(pr) if v > 0 else (0.0 if v == 0 else -1.0)
            out.append((k, r.season, r.game_id, u, "W" if v > 0 else "P" if v == 0 else "L"))
    return out


def summ(rows, B=2000, seed=3):
    if not rows:
        return None
    u = [x[3] for x in rows]
    w = sum(x[4] == "W" for x in rows)
    l = sum(x[4] == "L" for x in rows)
    rng = random.Random(seed)
    bs = sorted(st.mean(rng.choice(u) for _ in u) for _ in range(B))
    return {"n": len(u), "rec": f"{w}-{l}-{len(u) - w - l}", "win": 100 * w / max(1, w + l), "roi": 100 * st.mean(u),
            "lo": 100 * bs[int(.025 * B)], "hi": 100 * bs[int(.975 * B) - 1], "p": (sum(x <= 0 for x in bs) + 1) / (B + 1)}


def main(path):
    g = pd.read_csv(path)
    g = g[g.home_score.notna() & g.spread_line.notna() & g.total_line.notna() & (g.season >= 2009)]
    rows = bets(g)
    periods = {"2009-2017": (2009, 2017), "2018-2025": (2018, 2025), "2026": (2026, 2026), "2009-2025": (2009, 2025)}
    res = {}
    for rule in RULES:
        for name, (a, b) in periods.items():
            res[(rule, name)] = summ([x for x in rows if x[0] == rule and a <= x[1] <= b])
    ps = {r: res[(r, "2009-2025")]["p"] for r in RULES}
    ks = sorted(ps, key=ps.get)
    holm, stop = {}, False
    for i, k in enumerate(ks):
        stop = stop or ps[k] > 0.05 / (len(ks) - i)
        holm[k] = not stop
    for rule in RULES:
        print(rule, "| Holm (2009-2025):", "PASS" if holm[rule] else "fail")
        for name in periods:
            s = res[(rule, name)]
            if s:
                print(f"   {name:10s} n {s['n']:5d} {s['rec']:14s} win {s['win']:5.1f}% ROI {s['roi']:+6.1f}% "
                      f"[{s['lo']:+.1f}, {s['hi']:+.1f}] p {s['p']:.3f}")


if __name__ == "__main__":
    main(sys.argv[1])
