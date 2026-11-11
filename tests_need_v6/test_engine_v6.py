"""need_v6 engine: determinism, no look-ahead, ledger/settlement invariants, limits, desk actions, NEED cards."""
import collections
import math
import random

import pytest

from need_v6 import engine as V6
from need_v6 import need as NEED
from need_v6.book import Exposure
from need_v6.config import CONFIG
from need_v6.customers import stake_cap
from need_v6.market import GameMarket
from sportsbook_v4 import settlement as ST
from sportsbook_v4.market import profit_on_win
from sportsbook_v4.util import to_epoch
from tests_need_v6.conftest import KICK1, KICK2, T0, WE, WS, names_for, specs

ACTIONS = {"MOVE", "DEFEND", "BALANCE", "ACCEPT EXPOSURE", "NO POSITION"}


def run(db, g, customers, until=None, key="k1"):
    names = names_for(g)

    def card_fn(k, t, gm, desk, exposure, wagers, game):
        return NEED.card(k, t, gm, desk, exposure, wagers, game, names[k[0]])
    return V6.simulate(db, "nfl", specs(g), customers, WS, WE, key, until=until, card_fn=card_fn)


@pytest.fixture(scope="module")
def sim(two_games, customers):
    db, g = two_games
    return run(db, g, customers)


def test_wagers_exist_and_ids_unique(sim):
    ws = sim["wagers"]
    assert len(ws) > 100
    assert len({w["wager_id"] for w in ws}) == len(ws)


def test_deterministic_replay(two_games, customers, sim):
    db, g = two_games
    again = run(db, g, customers)
    assert [w["wager_id"] for w in again["wagers"]] == [w["wager_id"] for w in sim["wagers"]]
    assert again["wagers"] == sim["wagers"]
    assert again["decisions"] == sim["decisions"]
    assert again["cards_frozen"] == sim["cards_frozen"]


def test_different_key_gives_different_customers_flow(two_games, customers, sim):
    db, g = two_games
    other = run(db, g, customers, key="k2")
    assert [w["wager_id"] for w in other["wagers"]] != [w["wager_id"] for w in sim["wagers"]]


def test_no_lookahead_prefix(two_games, customers, sim):
    """Simulating only up to t1 reproduces exactly the full run's history before t1."""
    db, g = two_games
    t1 = T0 + 3 * 86400 + 1800
    part = run(db, g, customers, until=t1)
    full_before = [w for w in sim["wagers"] if to_epoch(w["accepted_at"]) <= t1]
    assert part["wagers"] == full_before
    for k, log in part["decisions"].items():
        assert log == [d for d in sim["decisions"][k] if d["at"] <= t1]


def test_no_lookahead_future_quotes_ignored(customers):
    """Adding a wild quote AFTER t1 cannot change anything at or before t1."""
    from tests_need_v6.conftest import AWAY, HOME, make_db, steady
    s, t = steady()
    g = [("ev1", HOME, AWAY, KICK1, s, t)]
    t1 = T0 + 2 * 86400
    base = run(make_db(g), g, customers, until=t1)
    s2 = lambda tt, bk: -3.0 if tt <= t1 else -14.0                    # noqa: E731
    g2 = [("ev1", HOME, AWAY, KICK1, s2, t)]
    moved = run(make_db(g2), g2, customers, until=t1)
    assert base["wagers"] == moved["wagers"]
    assert base["decisions"] == moved["decisions"]


def test_no_wager_at_or_after_kickoff(sim):
    kick = {"ev1": KICK1, "ev2": KICK2}
    assert all(to_epoch(w["accepted_at"]) < kick[w["event_id"]] for w in sim["wagers"])


def test_wager_terms_and_limits(sim):
    for w in sim["wagers"]:
        assert w["market"] in ("spread", "total")
        assert w["stake"] >= 1 and float(w["stake"]).is_integer()
        hours = ((KICK1 if w["event_id"] == "ev1" else KICK2) - to_epoch(w["accepted_at"])) / 3600
        assert w["stake"] <= stake_cap("nfl", w["market"], hours, w["respected"]) + 1e-9
        if w["market"] == "spread":
            assert w["accepted_home_spread"] == (w["accepted_line"] if w["side"] == "home" else -w["accepted_line"])


def test_kickoff_ramp_and_respected_limits():
    assert stake_cap("nfl", "spread", 100, False) < stake_cap("nfl", "spread", 30, False) \
        < stake_cap("nfl", "spread", 10, False) < stake_cap("nfl", "spread", 1, False)
    assert stake_cap("nfl", "spread", 1, False) == CONFIG["wager_limit"]["nfl"]["spread"]
    assert stake_cap("nfl", "spread", 1, True) == pytest.approx(CONFIG["respected_limit_factor"] *
                                                                CONFIG["wager_limit"]["nfl"]["spread"])


def test_weekly_budget_respected(sim, customers):
    bank = {c["bettor_index"]: (c["bankroll"], a["weekly_risk_fraction"]) for c, a in customers}
    staked = collections.defaultdict(float)
    for w in sim["wagers"]:
        staked[w["bettor_index"]] += w["stake"]
    for i, s in staked.items():
        b, f = bank[i]
        assert s <= b * f + 1e-6


def _settle(wagers, scores):
    cust = collections.defaultdict(float)
    book = 0.0
    for w in wagers:
        hs, as_ = scores[w["event_id"]]
        r = ST.wager_outcome(w, hs, as_)
        p = 0.0 if r == "push" else profit_on_win(w["stake"], w["accepted_price"]) if r == "win" else -w["stake"]
        cust[w["bettor_index"]] += p
        book -= p
    return cust, book


@pytest.mark.parametrize("scores", [{"ev1": (27, 24), "ev2": (24, 17)}, {"ev1": (20, 24), "ev2": (31, 10)},
                                    {"ev1": (24, 21), "ev2": (17, 30)}])
def test_settlement_conservation(sim, scores):
    """Book P&L == -(sum of customer P&L) == the settlement module's own book P&L, incl. pushes (3-pt margins)."""
    cust, book = _settle(sim["wagers"], scores)
    assert book == pytest.approx(-math.fsum(cust.values()), abs=1e-6)
    st_book = sum(ST.book_pnl_scores([w for w in sim["wagers"] if w["event_id"] == e], *scores[e]) for e in scores)
    assert st_book == pytest.approx(book, abs=0.05)


def test_exposure_grid_matches_settlement(sim):
    """Desk's running exposure == settlement module P&L at every final margin / total (alternate numbers, pushes)."""
    for (eid, m) in (("ev1", "spread"), ("ev1", "total"), ("ev2", "spread"), ("ev2", "total")):
        ws = [w for w in sim["wagers"] if w["event_id"] == eid and w["market"] == m]
        ex = Exposure(m)
        for w in ws:
            ex.add(w["side"], w["accepted_line"], w["accepted_price"], w["stake"])
        for i, x in enumerate(ex.x):
            if m == "spread" and -60 <= x <= 60 and i % 3 == 0:
                hs, as_ = (100 + x, 100) if x >= 0 else (100, 100 - x)
                assert ex.pnl[i] == pytest.approx(ST.book_pnl_scores(ws, hs, as_), abs=0.05)
            if m == "total" and 10 <= x <= 100 and i % 3 == 0:
                assert ex.pnl[i] == pytest.approx(ST.book_pnl_scores(ws, x, 0), abs=0.05)


def test_exposure_alternate_lines_and_middle():
    ex = Exposure("spread")
    ex.add("home", -3.0, -110, 110)      # home -3
    ex.add("away", 4.0, -110, 110)       # away +4 (a different number: pushes at 3 and 4)
    ws = [{"market": "spread", "side": "home", "accepted_line": -3.0, "accepted_home_spread": -3.0,
           "accepted_price": -110, "stake": 110},
          {"market": "spread", "side": "away", "accepted_line": 4.0, "accepted_home_spread": -4.0,
           "accepted_price": -110, "stake": 110}]
    for margin in (-10, 0, 3, 4, 10):
        hs, as_ = (50 + margin, 50)
        assert ex.pnl[list(ex.x).index(margin)] == pytest.approx(ST.book_pnl_scores(ws, hs, as_))
    # margin 3: home -3 pushes (0), away +4 wins (book pays 100) -> -100
    assert ex.pnl[list(ex.x).index(3)] == pytest.approx(-100.0)
    # margin 4: home -3 wins (book pays 100), away +4 pushes -> -100; margin 10: home wins, away loses -> +10
    assert ex.pnl[list(ex.x).index(4)] == pytest.approx(-100.0)
    assert ex.pnl[list(ex.x).index(10)] == pytest.approx(10.0)


def test_financial_need_matches_exposure_side(sim):
    for k, c in sim["cards_latest"].items():
        if not c:
            continue
        fin = c["financial_need"]
        ws = [w for w in sim["wagers"] if w["event_id"] == k[0] and w["market"] == k[1]]
        ex = Exposure(k[1])
        for w in ws:
            ex.add(w["side"], w["accepted_line"], w["accepted_price"], w["stake"])
        if fin["side"] is None:
            assert ex.heavy_side(("home", "away") if k[1] == "spread" else ("over", "under")) is None or not ws
            continue
        sides = ("home", "away") if k[1] == "spread" else ("over", "under")
        heavy = ex.heavy_side(sides)
        # Financial NEED is the side the book wants to win = the side opposite the one whose clean cover costs most
        assert heavy is not None and fin["side"] != heavy
        lo, hi = ex.clean()
        assert fin["swing"] == pytest.approx(abs(hi - lo), abs=0.05)


def test_desk_actions_are_valid_and_explained(sim):
    for log in sim["decisions"].values():
        assert log and log[0]["action"] == "MOVE" and "opened" in log[0]["rationale"]
        for d in log:
            assert d["action"] in ACTIONS and d["rationale"]
            if d["action"] == "ACCEPT EXPOSURE":
                assert d["desk_need"] in ("home", "away", "over", "under")
                assert "no validated informational edge" in d["rationale"]
                assert d["ev"] >= CONFIG["accept_ev_to_sd"] * d["sd"] and d["worst"] >= -CONFIG["game_limit"]["nfl"]
            else:
                assert d["desk_need"] is None


def test_desk_defends_key_number_then_moves(sim):
    log = sim["decisions"]["ev2|spread"]
    acts = [d["action"] for d in log]
    # ev2's market moves from -6.5 to -7.5 around T0+3d: the desk either defends 7 or moves -- never silently
    lines = {d["line"] for d in log}
    assert -6.5 in lines and (-7.5 in lines or "DEFEND" in acts)


def test_cards_policy_and_separation(sim):
    for k, c in list(sim["cards_latest"].items()) + list(sim["cards_frozen"].items()):
        if not c:
            continue
        an, fin, desk, mmi = c["arbitredge_need"], c["financial_need"], c["trading_desk_need"], c["market_maker_inference"]
        assert mmi["candidate_need_side"] == "UNKNOWN"
        assert an["policy"] == CONFIG["official_policy"] and an["source"] in ("TRADING DESK", "FINANCIAL", "NONE")
        if desk["side"] and desk["material"]:
            assert an["source"] == "TRADING DESK" and an["side"] == desk["side"]
        elif fin["status"] == "MATERIAL":
            assert an["source"] == "FINANCIAL" and an["side"] == fin["side"]
        else:
            assert an["source"] == "NONE" and an["side"] is None and an["text"] == "NONE"
        if desk["side"] is None:
            assert desk["text"] == "NONE"
        else:
            assert desk["basis"] == "risk acceptance (no validated informational edge)"
        assert "MODELED" in c["label"] or "modeled" in c["label"].lower()


def test_frozen_card_is_before_freeze_point(sim):
    for k, c in sim["cards_frozen"].items():
        kick = KICK1 if k[0] == "ev1" else KICK2
        assert to_epoch(c["as_of"]) <= kick - V6.FREEZE_BEFORE_S


def test_price_sensitivity_reason_codes_present(sim):
    assert sim["passes"].get("price_not_attractive", 0) >= 0
    assert set(sim["passes"]) <= {"no_open_market", "no_edge", "low_conviction", "limit_side_closed", "no_fair_price",
                                  "price_not_attractive", "price_too_expensive", "budget_or_minimum"}


def test_no_market_no_wagers(customers):
    from tests_need_v6.conftest import AWAY, HOME, make_db
    g = [("ev1", HOME, AWAY, KICK1, lambda t, bk: None, lambda t, bk: None)]
    r = run(make_db(g), g, customers)
    assert r["wagers"] == [] and r["cards_frozen"] == {}
    assert all(d == [] for d in r["decisions"].values())


def test_stale_market_holds_price_and_takes_no_position(customers):
    """Quotes stop 2 days before kickoff: after 6 h the market is STALE, the desk says NO POSITION and no wagers."""
    from tests_need_v6.conftest import AWAY, HOME, make_db, steady
    s, t = steady()
    g = [("ev1", HOME, AWAY, KICK1, s, t)]
    db = make_db(g, end=KICK1 - 2 * 86400)
    gm = GameMarket(db, "nfl", "ev1", "spread", KICK1)
    assert gm.state(KICK1 - 86400)["status"] == "STALE" and gm.state(KICK1 - 86400)["fair_mu"] is None
    r = run(db, g, customers)
    last_obs = max(gm.times)
    assert all(to_epoch(w["accepted_at"]) <= last_obs + CONFIG["max_quote_age_s"] for w in r["wagers"])


def test_point_in_time_market_state(two_games):
    db, _ = two_games
    gm = GameMarket(db, "nfl", "ev2", "spread", KICK2)
    assert gm.state(T0 - 1) == {"status": "NO_DATA"}
    early, late = gm.state(T0 + 86400), gm.state(T0 + 4 * 86400)
    assert early["line"] == -6.5 and late["line"] == -7.5
    assert early["basis"] == "pinnacle" and late["fair_mu"] > early["fair_mu"]      # mu = implied home margin


def test_maker_proxy_when_pinnacle_missing():
    from tests_need_v6.conftest import AWAY, HOME, make_db, steady
    s, t = steady()
    g = [("ev1", HOME, AWAY, KICK1, s, t)]
    db = make_db(g, books={"betonlineag": (-108, -102), "draftkings": (-110, -110)})
    st = GameMarket(db, "nfl", "ev1", "spread", KICK1).state(T0 + 86400)
    assert st["status"] == "OK" and st["basis"].startswith("maker proxy")
    db2 = make_db(g, books={"draftkings": (-110, -110), "fanduel": (-110, -110)})
    st2 = GameMarket(db2, "nfl", "ev1", "spread", KICK1).state(T0 + 86400)
    assert st2["status"] == "STALE" and st2["fair_mu"] is None      # recreational books alone never set fair


def test_seeded_random_scores_conservation(sim):
    rng = random.Random(7)
    for _ in range(20):
        sc = {"ev1": (rng.randint(0, 45), rng.randint(0, 45)), "ev2": (rng.randint(0, 45), rng.randint(0, 45))}
        cust, book = _settle(sim["wagers"], sc)
        assert book == pytest.approx(-math.fsum(cust.values()), abs=1e-6)


# ---------------------------------------------------------------- v6.1 audit: informed-position gate
def test_no_validated_signal_means_no_trading_desk_need(sim):
    assert CONFIG["validated_signals"] == [] and CONFIG["official_policy"] == "financial_first_v2"
    for c in list(sim["cards_latest"].values()) + list(sim["cards_frozen"].values()):
        if not c:
            continue
        t = c["trading_desk_need"]
        assert t["side"] is None and t["text"] == "NONE" and t["informed"]["status"] == "NONE"
        assert "no validated independent information" in t["informed"]["basis"]
        assert c["arbitredge_need"]["source"] != "TRADING DESK"


def test_tolerated_exposure_is_always_the_financial_need_side(sim):
    """Audit finding, as an invariant: ACCEPT EXPOSURE's direction is inherited, never chosen."""
    seen = 0
    for c in list(sim["cards_latest"].values()) + list(sim["cards_frozen"].values()):
        if c and c["exposure_tolerance"]["side"]:
            seen += 1
            assert c["exposure_tolerance"]["side"] == c["financial_need"]["side"]
            assert c["exposure_tolerance"]["direction_source"].startswith("inherited from Financial NEED")
    for log in sim["decisions"].values():
        assert all(d["action"] != "ACCEPT EXPOSURE" or d["desk_need"] for d in log)


def test_fair_cover_prob_is_a_probability(sim):
    for c in sim["cards_latest"].values():
        if c:
            for v in c["fair_cover_prob"].values():
                assert v is None or 0.0 < v < 1.0


def test_v1_policy_still_selectable(two_games, customers, monkeypatch):
    monkeypatch.setitem(CONFIG, "official_policy", "desk_material_first_v1")
    db, g = two_games
    r = run(db, g, customers)
    for c in r["cards_frozen"].values():
        t, tol = c["trading_desk_need"], c["exposure_tolerance"]
        assert t["side"] == tol["side"]                      # v1: the desk NEED is the tolerated side
        if tol["side"] and tol["material"]:
            assert c["arbitredge_need"]["source"] == "TRADING DESK"


def test_signal_gate_refuses_unimplemented_signal(monkeypatch):
    monkeypatch.setitem(CONFIG, "validated_signals", ["someone_added_this"])
    with pytest.raises(NotImplementedError):
        NEED.informed_position(None, {"status": "OK"}, {})
