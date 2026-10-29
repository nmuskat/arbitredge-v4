"""The dashboard-build speed-ups are pure memos: they must never change a single output."""

import random

import numpy as np

from edge_v4 import fairline
from market_v4 import store
from sims_v4 import distributions as D
from sportsbook_v4 import params as P
from sportsbook_v4 import population
from sportsbook_v4.market import MarketV4Source


def _market(seed=7, max_age=6 * 3600):
    rng = random.Random(seed)
    conn = store.connect()
    rows = []
    for book in ("pinnacle", "draftkings", "fanduel"):
        t = 1_000_000.0
        for _ in range(12):
            t += rng.choice([600, 1800, 3600, 4 * 3600, 9 * 3600])
            mlu = t - rng.choice([0, 30, 900, 7200, -120, -900])   # negative: provider clock ahead of the fetch
            line = rng.choice([-3.5, -3.0, -2.5])
            for side, pt, pr in (("home", line, rng.choice([-105, -110, -115])), ("away", -line, -110)):
                rows.append(("g1", "nfl", t + 86400, "H", "A", book, "spreads", side, pt, pr, mlu, t, "x", "fp"))
    conn.executemany("INSERT INTO mkt_quote (event_id, sport, commence, home, away, book, market, side, point, price, "
                     "market_last_update, fetched_at, source, request_fp) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return MarketV4Source(conn, max_age_seconds=max_age), sorted({r[11] for r in rows} | {r[10] for r in rows})


def test_equal_state_keys_mean_identical_quotes():
    m, points = _market()
    probe = sorted({p + d for p in points for d in (-1, 0, 0.5, 1)} |
                   {p + 6 * 3600 + d for p in points for d in (-1, 0, 1)})
    seen = {}
    for t in probe:
        k = m.state_key("g1", t)
        q = m.latest_quotes("g1", t)
        if k in seen:
            assert q == seen[k], t
        seen[k] = q
    assert len(seen) > 10


def test_population_cache_respects_parameter_variants():
    teams = ["nfl_a", "nfl_b"]
    import sqlite3
    from sportsbook_v4.schema import ensure_schema

    def pop_hash():
        c = sqlite3.connect(":memory:")
        ensure_schema(c)
        pid = population.ensure_population(c, "nfl", teams, bettor_count=200)
        return c.execute("SELECT population_hash FROM v4_population WHERE population_id=?", (pid,)).fetchone()[0]
    base = pop_hash()
    from sportsbook_v4.live_book import SENSITIVITY_VARIANTS
    with P.variant("sharp_share_6pct", **SENSITIVITY_VARIANTS["sharp_share_6pct"]()):
        varied = pop_hash()
    assert pop_hash() == base and varied != base


def test_discrete_normal_and_fair_line_memo_are_exact():
    rng = random.Random(3)
    for _ in range(300):
        mu, sg = rng.uniform(-20, 60), rng.uniform(4, 18)
        for vals in (D.M_RANGE, D.T_RANGE):
            ref = D._norm_cdf((vals + 0.5 - mu) / sg) - D._norm_cdf((vals - 0.5 - mu) / sg)
            assert np.array_equal(D.discrete_normal(vals, mu, sg), ref / ref.sum())
    km = D.KeyNumberModel(D.M_RANGE, True)
    km.w = {k: 1.0 + (k in (3, 7)) for k in range(81)}
    a = fairline.SpreadFair(km, 13.5, -3.0, 0.52)
    fairline._SOLVED.clear()
    b = fairline.SpreadFair(km, 13.5, -3.0, 0.52)
    assert a.mu == b.mu and a.pmf == b.pmf
    km.w = {**km.w, 10: 2.0}                       # refit: a new w is a new memo key
    assert fairline.SpreadFair(km, 13.5, -3.0, 0.52).pmf != a.pmf
