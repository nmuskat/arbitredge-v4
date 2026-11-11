"""Desk decision rules and price-sensitive customers, in isolation."""
import pytest

from need_v6 import customers as C
from need_v6.book import Exposure
from need_v6.config import CONFIG
from need_v6.desk import Desk, american, implied
from need_v6.market import GameMarket
from sportsbook_v4.facts import NoFacts
from tests_need_v6.conftest import AWAY, HOME, KICK1, T0, make_db, specs, steady


@pytest.fixture(scope="module")
def gm():
    s, t = steady(-3.0, 44.5)
    g = [("ev1", HOME, AWAY, KICK1, s, t)]
    return GameMarket(make_db(g), "nfl", "ev1", "spread", KICK1), g


def test_american_round_trip():
    for p in (0.3, 0.45, 0.5, 0.524, 0.6, 0.75):
        assert implied(american(p)) == pytest.approx(p, abs=0.003)


def test_desk_prices_carry_margin_and_respond_to_shade(gm):
    g, _ = gm
    d = Desk("nfl", "spread", g)
    ms = g.state(T0 + 86400)
    d.on_observation(T0 + 86400, ms, Exposure("spread"), KICK1)
    assert d.line == -3.0 and d.decision["action"] in ("NO POSITION", "MOVE")
    o = d.offers(ms["fair_mu"])
    over = implied(o["home"][1]) + implied(o["away"][1]) - 1
    assert over == pytest.approx(CONFIG["margin"], abs=0.01)
    d.shade = 0.02
    d.__dict__.pop("_offer_cache", None)
    o2 = d.offers(ms["fair_mu"])
    assert implied(o2["home"][1]) > implied(o["home"][1]) and implied(o2["away"][1]) < implied(o["away"][1])


def test_balance_when_worst_case_exceeds_limit(gm):
    g, _ = gm
    d = Desk("nfl", "spread", g)
    t = T0 + 86400
    ms = g.state(t)
    ex = Exposure("spread")
    d.on_observation(t, ms, ex, KICK1)
    ex.add("home", -3.0, -110, 40000)           # one-sided: worst case -36,364 > 25,000 limit
    d.on_wager(t + 60, "home", False, ms, ex)
    assert d.decision["action"] == "BALANCE" and "game limit" in d.decision["rationale"]
    assert d.shade > 0 or d.line < -3.0          # home made more expensive
    ex.add("home", -3.0, -110, 10000)            # beyond 1.25 x limit -> heavy side closed
    d.on_wager(t + 120, "home", False, ms, ex)
    assert "home" in d.closed and "home" not in d.offers(ms["fair_mu"])


def test_popular_side_alone_never_creates_desk_need(gm):
    """Lots of small recreational money on one side at a fair price: the desk does not oppose it automatically."""
    g, _ = gm
    d = Desk("nfl", "spread", g)
    t = T0 + 86400
    ms = g.state(t)
    ex = Exposure("spread")
    d.on_observation(t, ms, ex, KICK1)
    off = d.offers(ms["fair_mu"])
    for i in range(60):                           # 60% of tickets on home
        side = "home" if i % 5 < 3 else "away"
        ex.add(side, off[side][0], off[side][1], 100)
        d.on_wager(t + i, side, False, ms, ex)
    d.on_observation(t + 3 * 3600, ms, ex, KICK1)
    assert d.decision["desk_need"] is None and d.decision["action"] == "NO POSITION"


def test_accept_exposure_requires_ev_bar_and_settled_price(gm):
    g, _ = gm
    t = T0 + 86400
    ms = g.state(t)
    d = Desk("nfl", "spread", g)
    ex = Exposure("spread")
    d.on_observation(t, ms, ex, KICK1)
    ex.add("home", -3.0, -125, 15000)             # customers paid a big price on home: positive EV for the book
    d._evaluate_position(t + 3 * 3600, ms, ex)
    dec = d.decision
    if dec["action"] == "ACCEPT EXPOSURE":
        assert dec["desk_need"] == "away" and dec["ev"] >= CONFIG["accept_ev_to_sd"] * dec["sd"]
    else:
        assert dec["ev"] < CONFIG["accept_ev_to_sd"] * dec["sd"]
    d.last_respected = t + 3 * 3600 - 60          # a sharp just bet: price not settled -> no deliberate position
    d._evaluate_position(t + 3 * 3600, ms, ex)
    assert d.decision["action"] == "NO POSITION" and "too recent" in d.decision["rationale"]


def test_stale_observation_records_no_position(gm):
    g, _ = gm
    d = Desk("nfl", "spread", g)
    d.on_observation(T0 + 86400, g.state(T0 + 86400), Exposure("spread"), KICK1)
    d.on_observation(T0 + 86400 + 1, {"status": "STALE"}, Exposure("spread"), KICK1)
    assert d.decision["action"] == "NO POSITION" and d.decision.get("stale")


def _recs(customers):
    return [(c, a) for c, a in customers if not a["is_sharp"]]


def test_price_sensitivity_is_monotonic(gm, customers):
    """Same customers, same game, same seeds: a worse price on every side never yields more acceptances."""
    g, gl = gm
    game = specs(gl)[0]
    t = T0 + 2 * 86400
    ms = g.state(t)
    mu = ms["fair_mu"]
    counts = []
    for price in (-105, -110, -120, -135, -160):
        offers = {"home": (-3.0, price), "away": (3.0, price)}
        og = [(game, {"spread": {"desk": None, "gm": g, "ms": ms, "offers": offers}})]
        n = 0
        for c, a in _recs(customers)[:300]:
            for seq in range(3):
                w, why = C.decide("seed", c, a, seq, t, "nfl", og, NoFacts(), {})
                n += w is not None
        counts.append(n)
    assert counts == sorted(counts, reverse=True) and counts[0] > counts[-1]
    assert mu is not None


def test_sharps_bet_only_positive_ev(gm, customers):
    g, gl = gm
    game = specs(gl)[0]
    t = T0 + 2 * 86400
    ms = g.state(t)
    sharps = [(c, a) for c, a in customers if a["is_sharp"]][:40]
    fair = {"home": (-3.0, -110), "away": (3.0, -110)}
    gift = {"home": (-3.0, 120), "away": (3.0, -150)}
    for offers, expect in ((fair, False), (gift, True)):
        og = [(game, {"spread": {"desk": None, "gm": g, "ms": ms, "offers": offers}})]
        got = [C.decide("s", c, a, 0, t, "nfl", og, NoFacts(), {})[0] for c, a in sharps]
        if expect:
            assert any(w and w["side"] == "home" for w in got)
            assert all(w is None or w["side"] == "home" for w in got)
        else:
            assert all(w is None for w in got)


def test_closed_side_refused(gm, customers):
    g, gl = gm
    game = specs(gl)[0]
    t = T0 + 2 * 86400
    ms = g.state(t)
    og = [(game, {"spread": {"desk": None, "gm": g, "ms": ms, "offers": {"away": (3.0, -110)}}})]
    for c, a in _recs(customers)[:200]:
        w, why = C.decide("x", c, a, 0, t, "nfl", og, NoFacts(), {})
        assert w is None or w["side"] == "away"
