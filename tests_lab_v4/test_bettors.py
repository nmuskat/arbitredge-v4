"""lab_v4.bettors: no look-ahead in selection, grading, shrinkage, de-duplication, multiple testing."""
import random

from lab_v4 import bettors as BT


def w(uid, week, units_win=True, stake=100.0, side="home", gid="g", market="spread", line=-3.0, price=-110.0, p=0.5,
      arch="REC_CASUAL", sport="nfl"):
    win = 100 / 110 * stake
    return {"bettor_uid": uid, "week": week, "sport": sport, "archetype": arch, "market": market, "side": side,
            "accepted_line": line, "accepted_price": price, "stake": stake, "canonical_game_id": gid, "t": week * 1e5,
            "result": "win" if units_win else "loss", "customer_pnl": win if units_win else -stake,
            "units": (win if units_win else -stake) / stake, "p_fair_decision": p,
            "exp_units": p * (100 / 110) - (1 - p), "side_type": "favourite", "hours": 10.0, "kick": week * 1e5 + 36000}


def test_grade():
    assert BT.grade("spread", "home", -3.0, -110.0, (24, 20)) > 0
    assert BT.grade("spread", "home", -3.0, -110.0, (23, 20)) == 0.0          # push
    assert BT.grade("spread", "away", 3.0, -110.0, (30, 20)) == -1.0
    assert BT.grade("total", "under", 44.5, 100.0, (20, 20)) == 1.0
    assert BT.grade("moneyline", "away", None, 250.0, (10, 13)) == 2.5


def test_selection_uses_only_training_weeks():
    ws = [w(f"u{i}", wk, units_win=(i % 2 == 0)) for i in range(40) for wk in range(4)]
    rule = {"scope": lambda x: True, "direction": "best", "size": "k"}
    train = [x for x in ws if x["week"] < 3]
    a = BT.select(train, 1, rule, 5)
    # changing week-3 results (the evaluation week) cannot change the selected group
    ws2 = [dict(x, result="loss", customer_pnl=-x["stake"], units=-1.0) if x["week"] == 3 else x for x in ws]
    b = BT.select([x for x in ws2 if x["week"] < 3], 1, rule, 5)
    assert a == b and len(a) == 5 and all(int(u[1:]) % 2 == 0 for u in a)


def test_shrinkage_is_toward_zero_and_ordered():
    rng = random.Random(1)
    rows = []
    for i in range(200):
        xs = [w(f"u{i}", k, units_win=rng.random() < 0.5) for k in range(8)]
        rows.append(BT.summarize(xs, f"u{i}"))
    sh = BT.shrunk(rows)
    for r in rows:
        b = sh["bettors"].get(r["bettor_uid"])
        if b:
            assert abs(b["shrunk_excess"]) <= abs(r["excess_units"]) + 1e-12 and 0 <= b["B"] <= 1
    # pure noise: the estimated between-bettor variance should be small relative to sampling variance
    assert sh["tau2"] < sh["mean_se2"]


def test_evaluate_dedupes_correlated_bettors():
    g = {"side": "home", "point": -3.0, "price": -110.0, "units": 100 / 110, "exp": 0.0, "var": 1.0}
    rows = [{"sport": "nfl", "week": 3, "gid": "g1", "market": "spread", "tail": g, "fade": None} for _ in range(10)]
    r = BT.evaluate(rows, "tail")
    assert r["n"] == 1 and r["record"] == "1-0-0"                             # ten bettors on one side = one bet
    assert BT.evaluate(rows, "tail", dedupe=False)["n"] == 10


def test_bh():
    out = BT.bh({"a": 0.001, "b": 0.02, "c": 0.5, "d": 0.9}, q=0.10)
    assert out["a"] and out["b"] and not out["c"] and not out["d"]


def test_unit_var_positive():
    assert BT.unit_var(w("u", 1)) > 0.9


def test_rank_methods_select_extremes():
    ws = [w(f"u{i}", k, units_win=(i < 5)) for i in range(40) for k in range(3)]
    for rank in ("z", "pnl", "roi"):
        best = BT.select(ws, 1, {"scope": lambda x: True, "direction": "best", "size": "k", "rank": rank}, 5)
        worst = BT.select(ws, 1, {"scope": lambda x: True, "direction": "worst", "size": "k", "rank": rank}, 5)
        assert best == {f"u{i}" for i in range(5)} and not (best & worst)
