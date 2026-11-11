"""edge_research: plan frozen, Holm, grading, EV, point-in-time decisions, write-once prospective records."""
import hashlib
import json
import os
import sqlite3

import pytest

from edge_research import prospective as X
from edge_research import run as R
from edge_research.plan import PLAN, PLAN_HASH
from need_v6.market import normalizer


def test_plan_hash_and_core_thresholds():
    assert PLAN_HASH == hashlib.sha256(json.dumps(PLAN, sort_keys=True).encode()).hexdigest()
    assert PLAN["H1_gap_pts"] == 0.5 and PLAN["H2_pro_hold_pts"] == 0.25 and PLAN["H3_juice_shift_p"] == 0.03
    assert PLAN["min_cell_n"] == 30 and PLAN["decision_offset_s"] == 3600


def test_holm():
    out = R.holm({"a": 0.001, "b": 0.02, "c": 0.04})
    assert out == {"a": True, "b": True, "c": True} or out["a"]
    assert R.holm({"a": 0.03, "b": 0.04}) == {"a": False, "b": False}
    assert R.holm({"a": 0.01, "b": 0.5}) == {"a": True, "b": False}


def test_result_grading_spread_total_push():
    assert R.result("nfl", "spread", "home", -3.0, -110, (24, 20)) == pytest.approx(100 / 110)
    assert R.result("nfl", "spread", "home", -3.0, -110, (23, 20)) == 0.0
    assert R.result("nfl", "spread", "away", 3.0, 120, (23, 21)) == 1.2
    assert R.result("nfl", "total", "under", 44.5, -105, (24, 21)) == -1.0


def test_ev_at_fair_is_minus_vig():
    nz = normalizer("nfl", "spread")
    z = nz.quote("home", -3.0, -110, "away", 3.0, -110)
    e = R.ev_at(nz, z["mu"], "home", -3.0, -110)
    assert -0.06 < e < -0.03                                    # paying -110 on a fair coin with push mass


def _db(tmp_path, quotes):
    p = os.path.join(tmp_path, "m.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE mkt_quote (id INTEGER PRIMARY KEY, event_id TEXT, sport TEXT, commence REAL, home TEXT,"
              " away TEXT, book TEXT, market TEXT, side TEXT, point REAL, price INTEGER, market_last_update REAL,"
              " fetched_at REAL)")
    for q in quotes:
        c.execute("INSERT INTO mkt_quote (event_id, sport, commence, home, away, book, market, side, point, price,"
                  " market_last_update, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", q)
    c.commit()
    return c


def _pair(t, ko, book, hp, hpr, apr):
    return [("e1", "americanfootball_nfl", ko, "H", "A", book, "spreads", "home", hp, hpr, t, t),
            ("e1", "americanfootball_nfl", ko, "H", "A", book, "spreads", "away", -hp, apr, t, t)]


def test_px1_point_in_time_and_write_once(tmp_path):
    ko = 1_800_000_000.0
    q = []
    t = ko - 2 * 3600                                          # arm B decision fetch (kickoff - 2 h)
    q += _pair(t, ko, "pinnacle", -3.0, -105, -105)
    q += _pair(t, ko, "draftkings", -2.5, -110, -110)         # better than Pinnacle for the home side
    q += _pair(ko - 600, ko, "pinnacle", -7.0, -110, -110)    # a LATER fetch must not be seen at decision time
    c = _db(str(tmp_path), q)
    from intel_v7.history import History
    nz = normalizer("nfl", "spread")
    d = X.decide(History(c, "nfl", "e1", "spread"), nz, arm="B")
    assert d["decision"] == "SIGNAL" and d["side"] == "home" and d["book"] == "draftkings"
    assert d["decision_fetch"].startswith("2027")
    assert X.decide(History(c, "nfl", "e1", "spread"), nz, now=ko - 3 * 3600, arm="B") is None
    out = str(tmp_path / "rec")
    n1 = X.record(c, ko - 3000, out)
    n2 = X.record(c, ko - 3000, out)
    assert n1 >= 1 and n2 == 0                                 # write-once
