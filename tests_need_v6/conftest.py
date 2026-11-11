"""Shared fixtures for need_v6: a small synthetic quote store (append-only schema of market_v4) and a slice of the
real NFL population. Quotes here are TEST FIXTURES, never presented as real data."""
import sqlite3

import pytest

from sportsbook_v4 import customers_v5
from sportsbook_v4 import live_book as LB
from sportsbook_v4.engine import GameSpec
from sportsbook_v4.util import from_epoch

T0 = 1793700000 - (1793700000 % 3600)          # a fixed hour (2026-11-03), integer like the live engine
WS, WE = T0, T0 + 7 * 86400
HOME, AWAY = "Kansas City Chiefs", "Denver Broncos"
HOME2, AWAY2 = "Buffalo Bills", "Miami Dolphins"


def make_db(games, step_h=2, end=None, books=None):
    """games: [(event_id, home, away, kickoff, spread_fn(t)->home spread, total_fn(t)->total)]."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE mkt_quote (id INTEGER PRIMARY KEY, event_id TEXT, sport TEXT, commence REAL, home TEXT, "
              "away TEXT, book TEXT, market TEXT, side TEXT, point REAL, price INTEGER, market_last_update REAL, "
              "fetched_at REAL, source TEXT, request_fp TEXT)")
    c.execute("CREATE TABLE mkt_fetch (id INTEGER PRIMARY KEY, fetched_at REAL, sport TEXT, endpoint TEXT, "
              "request_fp TEXT, source TEXT, n_quotes INTEGER, n_anomalies INTEGER)")
    books = books or {"pinnacle": (-105, -105), "betonlineag": (-108, -102), "draftkings": (-110, -110),
                      "fanduel": (-112, -108)}
    rows = []
    t = T0
    while t < (end or max(g[3] for g in games)):
        for eid, h, a, kick, sfn, tfn in games:
            if t >= kick:
                continue
            for bk, (pa, pb) in books.items():
                s = sfn(t, bk)
                tot = tfn(t, bk)
                if s is not None:
                    rows += [(eid, "americanfootball_nfl", kick, h, a, bk, "spreads", "home", s, pa, t, t, "test", "fp"),
                             (eid, "americanfootball_nfl", kick, h, a, bk, "spreads", "away", -s, pb, t, t, "test", "fp")]
                if tot is not None:
                    rows += [(eid, "americanfootball_nfl", kick, h, a, bk, "totals", "over", tot, pa, t, t, "test", "fp"),
                             (eid, "americanfootball_nfl", kick, h, a, bk, "totals", "under", tot, pb, t, t, "test", "fp")]
        c.execute("INSERT INTO mkt_fetch (fetched_at, sport, endpoint, request_fp, source, n_quotes, n_anomalies) "
                  "VALUES (?,?,?,?,?,?,?)", (t, "americanfootball_nfl", "odds", "fp", "test", 1, 0))
        t += step_h * 3600
    c.executemany("INSERT INTO mkt_quote (event_id, sport, commence, home, away, book, market, side, point, price, "
                  "market_last_update, fetched_at, source, request_fp) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return c


def steady(spread=-3.0, total=44.5):
    return (lambda t, bk: spread if bk != "draftkings" else spread - 0.5), (lambda t, bk: total)


KICK1 = T0 + 4 * 86400 + 17 * 3600
KICK2 = T0 + 5 * 86400 + 18 * 3600


@pytest.fixture(scope="session")
def two_games():
    s1, t1 = steady(-3.0, 44.5)
    s2 = lambda t, bk: -6.5 if t < T0 + 3 * 86400 else -7.5          # noqa: E731  market moves through 7
    t2 = lambda t, bk: 47.5                                              # noqa: E731
    g = [("ev1", HOME, AWAY, KICK1, s1, t1), ("ev2", HOME2, AWAY2, KICK2, s2, t2)]
    return make_db(g), g


def specs(g):
    return [GameSpec(eid, LB._team_key("nfl", h, None), LB._team_key("nfl", a, None), from_epoch(k), False, False)
            for eid, h, a, k, *_ in g]


@pytest.fixture(scope="session")
def customers():
    from need_v6 import week as W
    _, pop = W.population("nfl", [], None)
    out, sharps = [], []
    for b in pop["bettors"]:
        c, arch, active, _ = customers_v5.effective(b, b["bankroll"], WS)
        if not active:
            continue
        (sharps if arch["is_sharp"] else out).append((c, arch))
    return out[:500] + sharps[:60]


def names_for(g):
    return {eid: {"home": h, "away": a, "over": "Over", "under": "Under"} for eid, h, a, *_ in g}
