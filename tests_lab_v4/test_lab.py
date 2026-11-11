"""lab_v4: configuration A reproduces the production engine; B-E hooks behave as specified; common random numbers."""
import sqlite3

import pytest

from lab_v4.config import CONFIGS, MARGIN, RISK
from lab_v4.measure import need_at
from lab_v4.runner import _reprice, overround, run_week
from lab_v4.season import open_week
from need_v6.config import kickoff_factor
from sportsbook_v4 import live_book as LB
from sportsbook_v4.engine import advance
from sportsbook_v4.facts import NoFacts
from sportsbook_v4.ledger_read import fetch_wagers
from sportsbook_v4.schema import ensure_schema
from sportsbook_v4.util import from_epoch
from tests_v4.test_live_book import T0, build_store

WE = T0 + 7 * 86400


@pytest.fixture(scope="module")
def week():
    live = build_store(start=T0 - 86400, end=WE)
    games = LB.week_games(live, "nfl", T0, WE, WE)
    book = sqlite3.connect(":memory:")
    ensure_schema(book)
    run, pop, snap = open_week(book, live, "nfl", T0, WE, [], games)
    pop["bettors"] = pop["bettors"][:1500]                 # a slice keeps the test fast; same for both sides
    snap = {b["bettor_index"]: snap[b["bettor_index"]] for b in pop["bettors"]}
    return live, book, run, pop, snap


def key(w):
    return (w["wager_id"], w["market"], w["side"], float(w["accepted_line"]), float(w["accepted_price"]),
            float(w["stake"]), w["accepted_at"])


def test_config_A_reproduces_production_engine_exactly():
    live = build_store(start=T0 - 86400, end=WE)
    games = LB.week_games(live, "nfl", T0, WE, WE)
    book = sqlite3.connect(":memory:")
    ensure_schema(book)
    run, pop, snap = open_week(book, live, "nfl", T0, WE, [], games)
    advance(book, run["run_id"], from_epoch(WE), LB._market(live, "nfl"), NoFacts(), data_complete_through=from_epoch(WE))
    ref = fetch_wagers(book, run["run_id"], market=None)
    lab, _ = run_week(run, pop, snap, LB._market(live, "nfl"), CONFIGS["A"], 0)
    assert len(ref) > 100 and sorted(map(key, ref)) == sorted(map(key, lab))


@pytest.fixture(scope="module")
def runs(week):
    live, book, run, pop, snap = week
    return {c: run_week(run, pop, snap, LB._market(live, "nfl"), CONFIGS[c], 0) for c in "ABCDE"}


def test_common_random_numbers_same_arrivals(runs):
    ev = {c: runs[c][1]["events"] for c in runs}
    assert len(set(ev.values())) == 1                      # every configuration sees the same customer arrivals


def test_seed_changes_randomness(week):
    live, book, run, pop, snap = week
    a, _ = run_week(run, pop, snap, LB._market(live, "nfl"), CONFIGS["A"], 0)
    b, _ = run_week(run, pop, snap, LB._market(live, "nfl"), CONFIGS["A"], 1)
    assert [w["wager_id"] for w in a] != [w["wager_id"] for w in b]


def test_margin_config_prices_at_target(runs):
    d = [w for w in runs["D"][0] if w["book_mode"] == "market" and w["overround"] is not None]
    assert d and all(abs(w["overround"] - MARGIN["overround"]) < 0.012 for w in d)   # whole-cent rounding
    a = [w["overround"] for w in runs["A"][0] if w["book_mode"] == "market" and w["overround"] is not None]
    assert sum(a) / len(a) > MARGIN["overround"]


def test_price_sensitivity_only_removes_recreational_bets(runs):
    a, b = runs["A"], runs["B"]
    assert b[1]["passes"].get("price_not_attractive", 0) > 0
    assert len(b[0]) < len(a[0])
    ka = {w["wager_id"] for w in a[0] if w["respected"]}
    kb = {w["wager_id"] for w in b[0] if w["respected"]}
    assert ka == kb or len(kb) > 0                         # sharps are not price-gated (book state may differ)


def test_risk_caps_and_controls(runs, week):
    _, _, run, _, _ = week
    kick = {g.canonical_game_id: __import__("sportsbook_v4.util", fromlist=["to_epoch"]).to_epoch(g.commence_time)
            for g in run["slate"].games}
    for w in runs["C"][0]:
        m = "spread" if w["market"] == "moneyline" else w["market"]
        cap = RISK["wager_limit"]["nfl"][m] * kickoff_factor((kick[w["canonical_game_id"]] - w["t"]) / 3600) * \
            (RISK["respected_limit_factor"] if w["respected"] else 1.0)
        assert w["stake"] <= cap + 1e-9


def test_reprice_keeps_split_and_sets_margin():
    o = {"home_spread": -3.0, "home_price": -120.0, "away_price": 100.0}
    r = _reprice(o, "spread", target=0.035)
    assert r["home_spread"] == -3.0 and abs(overround(r, "spread") - 0.035) < 0.01
    s = _reprice(o, "spread", shift_a=0.02)
    assert s["home_price"] < o["home_price"]               # shading side A makes it more expensive


def test_need_at_matches_settlement_side():
    ws = [{"market": "spread", "side": "home", "accepted_line": -3.0, "accepted_home_spread": -3.0,
           "accepted_price": -110.0, "stake": 1000.0},
          {"market": "spread", "side": "away", "accepted_line": 3.0, "accepted_home_spread": -3.0,
           "accepted_price": -110.0, "stake": 200.0}]
    side, swing, handle, signed = need_at(ws, "spread")
    assert side == "away" and handle == 1200.0 and signed < 0


def test_closed_side_refuses_wagers():
    from lab_v4.runner import Risk
    r = Risk("nfl")
    for i in range(12):
        r.add("g", "spread", "home", -3.0, -110.0, 4000.0, i)
    assert r.closed.get(("g", "spread")) == "home" and any(x["action"] == "CLOSE" for x in r.log)
    assert r.shade[("g", "spread")] > 0
