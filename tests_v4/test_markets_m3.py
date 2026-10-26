"""m3: totals + moneyline markets, exposure for every market, freshness via market_v4."""
import sqlite3
import time

import pytest

from market_v4 import store
from market_v4.normalize import Quote as MQuote
from sportsbook_v4 import settlement as S
from sportsbook_v4.engine import GameSpec, SlateSpec, advance, create_run
from sportsbook_v4.ledger_read import fetch_wagers
from sportsbook_v4.market import FixtureMarket, MarketV4Source, quote
from sportsbook_v4.population import ensure_population
from sportsbook_v4.schema import ensure_schema
from sportsbook_v4.util import parse_ts
from tests_v4 import fixtures as fx

TOTALS = {"g_tnf": [44.5, 45.5], "g_sun1": [47.0, 46.5], "g_sun2": [41.5, 42.5], "g_sun3": [38.0, 38.0],
          "g_sun4": [44.0, 43.5], "g_snf": [48.5, 49.5], "g_mnf": [51.0, 51.0]}


def ml_from_spread(hs):
    """Rough moneyline pair for a spread (fixture convenience, not a model)."""
    table = {0: (-110, -110), 1: (-120, 100), 2: (-130, 110), 3: (-160, 135), 4: (-185, 155), 6: (-250, 205),
             7: (-300, 240), 10: (-450, 340)}
    k = min(table, key=lambda x: abs(x - abs(hs)))
    fav, dog = table[k]
    return (fav, dog) if hs < 0 else ((dog, fav) if hs > 0 else (-110, -110))


def build_all_markets():
    quotes = {}
    for gid, _h, _a, _ko, _s, _tv, path in fx.NFL_WEEK:
        qs = []
        for i, (ts, hs, hp, ap) in enumerate(path):
            t = parse_ts(ts)
            tot = TOTALS[gid][min(i, 1)]
            hm, am = ml_from_spread(hs)
            for book in fx.BOOKS:
                bt = t if (book in fx.REFERENCE_BOOKS or i == 0) else t + fx.RETAIL_LAG
                qs.append(quote(book, bt.isoformat(), hs, hp, ap, ref=f"fx:{gid}:{book}:{i}",
                                total=tot, home_ml=hm, away_ml=am))
        quotes[gid] = qs
    return FixtureMarket(quotes, source_id="fixture:nfl_week_all_markets_v1")


@pytest.fixture(scope="module")
def all_markets_week(tmp_path_factory):
    path = tmp_path_factory.mktemp("m3") / "m3.db"
    conn = sqlite3.connect(path)
    ensure_schema(conn)
    pid = ensure_population(conn, "nfl", fx.NFL_TEAMS)
    market, slate, facts = build_all_markets(), fx.build_slate(), fx.build_facts()
    run_id = create_run(conn, pid, slate, market)
    t = time.time()
    res = advance(conn, run_id, fx.WINDOW_END, market, facts, data_complete_through=fx.WINDOW_END)
    res["seconds"] = time.time() - t
    yield conn, run_id, res
    conn.close()


def test_wagers_in_every_market(all_markets_week):
    conn, run_id, res = all_markets_week
    counts = dict(conn.execute("SELECT market, COUNT(*) FROM v4_wager WHERE run_id=? GROUP BY market", (run_id,)))
    assert counts.get("spread", 0) > 1000 and counts.get("total", 0) > 300 and counts.get("moneyline", 0) > 200
    sides = {r for r in conn.execute("SELECT DISTINCT market||':'||side FROM v4_wager WHERE run_id=?", (run_id,))}
    assert {("total:over",), ("total:under",), ("moneyline:home",), ("moneyline:away",)} <= sides


def test_10k_population_week_runs_in_reasonable_time(all_markets_week):
    conn, run_id, res = all_markets_week
    n = conn.execute("SELECT bettor_count FROM v4_population").fetchone()[0]
    assert n == 10_000 and res["seconds"] < 120


def test_spread_exposure_ignores_other_markets(all_markets_week):
    conn, run_id, _ = all_markets_week
    allw = fetch_wagers(conn, run_id, "g_snf", market=None)
    spread = fetch_wagers(conn, run_id, "g_snf")
    assert len(allw) > len(spread) and all(w["market"] == "spread" for w in spread)
    assert S.exposure(allw)["tickets"] == S.exposure(spread)["tickets"]


def test_need_for_every_market_and_joint_book(all_markets_week):
    conn, run_id, _ = all_markets_week
    ws = fetch_wagers(conn, run_id, "g_snf", market=None)
    need = S.book_need_all(ws, ref_home_spread=-3.5, ref_total=49.5, max_score=60)
    assert need["total"]["financial_need_side"] in ("over", "under", None)
    assert need["moneyline"]["financial_need_side"] in ("home", "away", None)
    j = need["joint"]
    assert j["worst_case"]["pnl"] <= j["best_case"]["pnl"]
    assert set(j["regions"]) == {"home_covers+over", "home_covers+under", "away_covers+over", "away_covers+under"}
    # exactness: the joint best/worst equal brute force over the same scores
    brute = [S.book_pnl_scores(ws, h, a) for h in range(61) for a in range(61)]
    assert j["best_case"]["pnl"] == max(brute) and j["worst_case"]["pnl"] == min(brute)


def test_schema_rejects_inconsistent_wagers(all_markets_week):
    conn, run_id, _ = all_markets_week
    cur = conn.execute("SELECT * FROM v4_wager WHERE run_id=? AND market='total' LIMIT 1", (run_id,))
    base = dict(zip([d[0] for d in cur.description], cur.fetchone()))
    cols = list(base)
    for bad in ({"side": "home"}, {"accepted_home_spread": -3.0}, {"market": "moneyline", "accepted_line": 2.5}):
        row = dict(base, wager_id="bad" + str(len(bad)) + list(bad)[0], arrival_seq=10_000 + len(bad), **bad)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"INSERT INTO v4_wager ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                         [row[c] for c in cols])
    conn.rollback()


# ---------------- pure settlement
def w(market, side, line, price=-110, stake=100.0):
    return {"market": market, "side": side, "accepted_line": line, "accepted_price": price, "stake": stake,
            "accepted_home_spread": line if (market == "spread" and side == "home") else
            (-line if market == "spread" else None)}


def test_total_settlement_push_and_middle():
    ws = [w("total", "over", 44.0), w("total", "under", 46.0)]
    e = S.total_exposure(ws)
    by = dict(e["pnl_by_total"])
    assert by[44] == pytest.approx(-90.91, abs=0.01)    # over pushes, under wins
    assert by[45] == pytest.approx(-181.82, abs=0.01)   # middle: both win -> book's worst
    assert 45 in e["middle_risk_totals"] and e["push_totals"] == [44.0, 46.0]


def test_moneyline_tie_is_no_action():
    ws = [w("moneyline", "home", 0.0, -150, 150.0), w("moneyline", "away", 0.0, 130, 100.0)]
    e = S.moneyline_exposure(ws)
    assert e["pnl_if_tie"] == 0.0
    assert e["pnl_if_home_wins"] == pytest.approx(-100.0 + 100.0)   # pay 100 on home, keep 100 on away
    assert e["pnl_if_away_wins"] == pytest.approx(150.0 - 130.0)
    assert e["financial_need_side"] == "away"


def test_wager_outcome_dispatch():
    assert S.wager_outcome(w("spread", "home", -3.0), 24, 21) == "push"
    assert S.wager_outcome(w("total", "under", 45.0), 24, 20) == "win"
    assert S.wager_outcome(w("moneyline", "away", 0.0), 17, 17) == "push"


# ---------------- freshness through the V4 market store
def _store_with_gap(gid, kick):
    conn = store.connect()
    t0 = parse_ts(fx.WINDOW_START).timestamp()
    gap = (t0 + 2 * 86400, t0 + 4 * 86400)                       # 48 h provider outage
    t = t0
    while t < parse_ts(kick).timestamp():
        if not (gap[0] < t < gap[1]):
            iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
            qs = []
            for book in fx.BOOKS:
                for mk, side, pt, pr in (("spreads", "home", -3.0, -110), ("spreads", "away", 3.0, -110),
                                         ("totals", "over", 44.5, -110), ("totals", "under", 44.5, -110),
                                         ("h2h", "home", None, -160), ("h2h", "away", None, 135)):
                    qs.append(MQuote(gid, "americanfootball_nfl", kick, "H", "A", book, mk, side, pt, pr, iso, iso,
                                     "odds_api", "fp"))
            store.ingest(conn, qs, [], iso, "americanfootball_nfl", "odds", "fp")
        t += 30 * 60
    return conn, gap


def test_no_wagers_accepted_while_quotes_are_stale():
    gid, kick = "e" * 32, "2026-11-01T18:00:00Z"
    mconn, gap = _store_with_gap(gid, kick)
    market = MarketV4Source(mconn, max_age_seconds=45 * 60)
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    pid = ensure_population(conn, "nfl", ["nfl_buf", "nfl_dal"], bettor_count=2000)
    slate = SlateSpec("nfl", "gap_test", fx.WINDOW_START, kick,
                      (GameSpec(gid, "nfl_buf", "nfl_dal", kick, False, False),))
    run_id = create_run(conn, pid, slate, market)
    res = advance(conn, run_id, kick, market, fx.build_facts(), data_complete_through=kick)
    times = [parse_ts(t).timestamp() for (t,) in conn.execute("SELECT accepted_at FROM v4_wager")]
    assert res["accepted"] > 100
    stale_from = gap[0] + 45 * 60 + 60                           # last pre-gap quote + max age (+offer lag slack)
    assert not [t for t in times if stale_from + 600 < t < gap[1]]
    assert res["passes"].get("no_open_market", 0) > 0
    assert {m for (m,) in conn.execute("SELECT DISTINCT market FROM v4_wager")} == {"spread", "total", "moneyline"}
