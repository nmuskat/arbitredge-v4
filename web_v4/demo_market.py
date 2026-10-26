"""
web_v4/demo_market.py -- SIMULATED multi-book quote histories for UI demos.

Every row is tagged source='demo_sim' and every page that uses it shows a
DEMO badge. Paths are anchored so that the consensus at the snapshot time
equals the game's real listed line (games.csv); everything before that is
invented. Market-making books lead moves; recreational books follow 1-3 h
later; recreational books occasionally hang an outlier/stale number.
Nothing here is observed market data.
"""

import random
import time

from edge_v4 import pricing
from edge_v4.fairline import SpreadFair
from market_v4 import store
from market_v4.normalize import Quote
from sims_v4 import distributions as D

SHARP = ["pinnacle", "circasports", "lowvig", "betonlineag"]
REC = ["draftkings", "fanduel", "betmgm", "williamhill_us"]
SOURCE = "demo_sim"


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _pair(p, vig):
    return (pricing.decimal_to_american(1 / (p * (1 + vig))), pricing.decimal_to_american(1 / ((1 - p) * (1 + vig))))


def _path(rng, end_value, start_t, end_t, step, n_moves, move_size):
    """Piecewise-constant consensus path ending at end_value. Returns sorted [(t, value)]."""
    times = sorted(rng.uniform(start_t + 3600, end_t - 3600) for _ in range(n_moves))
    moves = [rng.choice((-1, 1)) * move_size for _ in times]
    v0 = end_value - sum(moves)
    out, v = [(start_t, v0)], v0
    for t, m in zip(times, moves):
        v += m
        out.append((t, v))
    return out


def _at(path, t, lag=0.0):
    v = path[0][1]
    for pt, pv in path:
        if pt + lag <= t:
            v = pv
    return v


def build(conn, games, as_of, spread_kn, spread_sigma, seed=2026, days=5, step_s=1800):
    """games: [{'event_id','home','away','kickoff' (epoch),'spread_line' (home-favoured +),'total_line'}]."""
    rng = random.Random(seed)
    start = as_of - days * 86400
    ml_cache = {}

    def ml_prob(line, pj):
        k = (line, round(pj, 4))
        if k not in ml_cache:
            fair = SpreadFair(spread_kn, spread_sigma, line, pj)
            w, _tie, l = D.moneyline_probs(fair.pmf)
            ml_cache[k] = w / (w + l)
        return ml_cache[k]

    n = 0
    for g in games:
        listed_home_line = -float(g["spread_line"])          # home handicap
        sp = _path(rng, listed_home_line, start, as_of, step_s, rng.choice((0, 1, 1, 2)), 0.5)
        tp = _path(rng, float(g["total_line"]), start, as_of, step_s, rng.choice((0, 1, 2)), 0.5)
        juice = _path(rng, 0.0, start, as_of, step_s, rng.choice((0, 1)), 0.015)   # price-only drift on cover prob
        outlier = {b: (rng.random() < 0.35, rng.choice((-0.5, 0.5))) for b in REC}
        t = start
        while t <= as_of and t < g["kickoff"]:
            qs = []
            for book in SHARP + REC:
                lag = 0.0 if book in SHARP else rng.choice((3600.0, 7200.0, 10800.0))
                line = _at(sp, t, lag)
                total = _at(tp, t, lag)
                pj = 0.5 + _at(juice, t, lag)
                vig = 0.02 if book in SHARP else 0.045
                if book in REC and outlier[book][0] and t > as_of - 6 * 3600:
                    line += outlier[book][1]                     # a stale/outlier number near the snapshot
                hp, ap = _pair(pj, vig)
                op, up = _pair(0.5, vig)
                hm, am = _pair(ml_prob(line, pj), vig + 0.005)
                ts = _iso(t)
                for mk, side, pt, pr in (("spreads", "home", line, hp), ("spreads", "away", -line, ap),
                                         ("totals", "over", total, op), ("totals", "under", total, up),
                                         ("h2h", "home", None, hm), ("h2h", "away", None, am)):
                    qs.append(Quote(g["event_id"], "americanfootball_nfl", _iso(g["kickoff"]), g["home"], g["away"],
                                    book, mk, side, pt, int(pr), ts, ts, SOURCE, "demo"))
            store.ingest(conn, qs, [], ts, "americanfootball_nfl", "demo", f"demo:{g['event_id']}", source=SOURCE)
            n += len(qs)
            t += step_s
    return n
