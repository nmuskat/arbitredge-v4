import sqlite3

import numpy as np
import pytest

from edge_v4 import backtest as B
from edge_v4 import ev, ledger
from edge_v4.fairline import SpreadFair, TotalFair
from sims_v4 import distributions as D


@pytest.fixture(scope="module")
def kn():
    rng = np.random.default_rng(0)
    mus = rng.normal(0, 6, 3000)
    obs = np.where(rng.random(3000) < 0.10, np.sign(rng.normal(mus, 13)) * 3, np.rint(rng.normal(mus, 13))).astype(int)
    return D.KeyNumberModel(D.M_RANGE, True).fit_ipf(mus, obs, 13.0, iters=20)


def test_fairline_round_trip_and_key_number_value(kn):
    sf = SpreadFair(kn, 13.0, -3.0, 0.52)
    pw, pu = sf.side_probs("home", -3.0)
    assert pw / (1 - pu) == pytest.approx(0.52, abs=1e-3)
    # half a point across 3 is worth more than half a point across 5
    gain3 = sf.side_probs("home", -2.5)[0] - sf.side_probs("home", -3.0)[0]
    gain5 = sf.side_probs("home", -4.5)[0] - sf.side_probs("home", -5.0)[0]
    assert gain3 > gain5 > 0
    a_w, a_p = sf.side_probs("away", 3.0)
    assert a_p == pytest.approx(pu) and a_w == pytest.approx(1 - pw - pu)


def test_total_fair_round_trip():
    tk = D.KeyNumberModel(D.T_RANGE, False)
    tk.w = {int(t): 1.0 for t in D.T_RANGE}
    tf = TotalFair(tk, 13.5, 44.5, 0.55)
    assert tf.side_probs("over", 44.5)[0] == pytest.approx(0.55, abs=1e-3)


def test_ledger_is_append_only_and_registry_versioned():
    c = ledger.connect()
    ledger.register(c, "s", "1", "d", {"a": 1}, "2026-01-01T00:00:00Z")
    ledger.register(c, "s", "1", "d", {"a": 1}, "2026-01-01T00:00:00Z")      # idempotent
    with pytest.raises(ValueError, match="bump the version"):
        ledger.register(c, "s", "1", "d", {"a": 2}, "2026-01-01T00:00:00Z")
    assert ledger.strategies_tested(c) == 1
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("DELETE FROM v4e_strategy")


def test_decision_must_precede_kickoff_and_grading_is_exact():
    c = ledger.connect()
    ledger.register(c, "s", "1", "d", {}, "2026-01-01T00:00:00Z")
    row = {"game_id": "g", "market": "spreads", "selection": "home", "book": "x", "line": -3.0, "american": -110,
           "decision": "BET"}
    with pytest.raises(sqlite3.IntegrityError):
        ledger.record_decision(c, "s", "1", "backtest", row, "2026-01-02T18:00:00Z", "2026-01-02T17:00:00Z")
    did = ledger.record_decision(c, "s", "1", "backtest", row, "2026-01-02T16:00:00Z", "2026-01-02T17:00:00Z")
    bid = ledger.record_bet(c, did, "s", "1", "backtest", "g", "spreads", "home", -3.0, -110, 1.0,
                            "2026-01-02T16:00:00Z", "2026-01-02T17:00:00Z", 0.5, 0.1, 0.0, "test")
    assert ledger.grade(c, bid, 24, 21, "2026-01-03T00:00:00Z", "test") == ("P", 0.0)
    with pytest.raises(sqlite3.IntegrityError):                  # graded once
        ledger.grade(c, bid, 24, 20, "2026-01-03T00:00:00Z", "test")
    for (mk, side, line, hs, as_, exp) in (("totals", "under", 44.5, 24, 20, "W"), ("h2h", "away", 0.0, 17, 24, "W"),
                                           ("spreads", "away", 3.0, 24, 20, "L")):
        did = ledger.record_decision(c, "s", "1", "backtest", dict(row, market=mk, selection=side, line=line),
                                     "2026-01-02T16:00:00Z", "2026-01-02T17:00:00Z")
        bid = ledger.record_bet(c, did, "s", "1", "backtest", "g", mk, side, line, -110, 1.0,
                                "2026-01-02T16:00:00Z", "2026-01-02T17:00:00Z", 0.5, 0.0, 0.0, "test")
        assert ledger.grade(c, bid, hs, as_, "2026-01-03T00:00:00Z", "test")[0] == exp


@pytest.mark.parametrize("scenario", ["positive", "negative", "noisy_consensus"])
def test_synthetic_controls(scenario):
    c = ledger.connect()
    sid = B.synthetic_line_shop(c, scenario, n_games=1200, seed=11)
    r = B.summarize(c, sid, n_boot=500)
    if scenario == "negative":
        assert r["bets"] == 0                                    # no edge -> no bets at all
    elif scenario == "positive":
        s = r["summary"]
        assert s["mean_clv"] > 0.02 and s["record"]["roi"] > 0 and s["roi_ci"]["roi_lo"] > 0
    else:
        s = r["summary"]
        assert s["mean_clv"] < 0 and s["edge_status"] == "NOT PROVEN"   # illusions must not be certified
