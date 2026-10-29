"""Simulated-bettor settlement and bankrolls: immutable, cent-exact, corrections superseded, nothing guessed."""

import pytest

from sportsbook_v4 import bettor_settlement as BS

POP = "nfl|v|n10|teams:x"


def w(i, market="spread", side="home", line=-3.0, price=-110, stake=100.0, bettor=0, run="r1", game="g1"):
    return {"wager_id": f"w{i}", "run_id": run, "population_id": POP, "bettor_index": bettor, "archetype": "REC_CASUAL",
            "canonical_game_id": game, "sport": "nfl", "market": market, "side": side, "accepted_line": line,
            "accepted_price": price, "stake": stake}


def res(h, a, sid="2026_09_X", at="2026-11-02T13:00:00Z", corrected=False):
    return {"home_score": h, "away_score": a, "source": "nflverse games.csv", "source_id": sid, "fetched_at": at,
            "corrected": corrected}


@pytest.mark.parametrize("result,price,stake,payout,pnl", [
    ("win", -110, 100.0, 19091, 9091), ("win", 150, 40.0, 10000, 6000), ("push", -110, 55.0, 5500, 0),
    ("loss", -110, 77.0, 0, -7700)])
def test_amounts_in_cents(result, price, stake, payout, pnl):
    assert BS.amounts(result, stake, price) == (payout, pnl)


@pytest.mark.parametrize("wager,score,result", [
    (w(1, line=-3.0), (24, 21), "push"), (w(1, line=-2.5), (24, 21), "win"), (w(1, side="away", line=3.0), (24, 20), "loss"),
    (w(1, market="total", side="over", line=44.5), (24, 21), "win"), (w(1, market="total", side="under", line=45.0), (24, 21), "push"),
    (w(1, market="moneyline", side="away", line=0), (17, 17), "push"), (w(1, market="moneyline", side="home", line=0), (20, 17), "win")])
def test_each_market_graded_at_its_own_number(wager, score, result):
    assert BS.settle_one(wager, res(*score), "t")["result"] == result


def test_settlement_is_idempotent_and_corrections_supersede_never_overwrite():
    ws = [w(1), w(2, side="away", line=3.0)]
    first = BS.new_settlements(ws, {("nfl", "g1"): res(24, 20)}, [], "t1")
    assert [s["result"] for s in first] == ["win", "loss"]
    assert BS.new_settlements(ws, {("nfl", "g1"): res(24, 20)}, first, "t2") == []          # same score: nothing new
    fixed = BS.new_settlements(ws, {("nfl", "g1"): res(23, 20, corrected=True)}, first, "t3")
    assert [s["result"] for s in fixed] == ["push", "push"]
    assert [s["supersedes"] for s in fixed] == [s["settlement_id"] for s in first]
    cur = BS.latest(first + fixed)
    assert cur["w1"]["result"] == "push" and len(first + fixed) == 4                         # history kept
    assert BS.verify(first + fixed, {x["wager_id"]: x for x in ws}) == []


def test_no_score_means_open_and_nothing_is_guessed():
    assert BS.new_settlements([w(1)], {}, [], "t") == []


def test_tampering_and_broken_chains_are_detected():
    ws = [w(1)]
    s = BS.new_settlements(ws, {("nfl", "g1"): res(30, 20)}, [], "t")
    bad = dict(s[0], pnl_cents=s[0]["pnl_cents"] + 1)
    assert BS.verify([bad], {"w1": ws[0]})
    assert BS.verify(s, {}) and "unknown wager" in BS.verify(s, {})[0]
    orphan = dict(s[0], settlement_id="z", supersedes="missing")
    assert any("unknown row" in p for p in BS.verify([orphan], {"w1": ws[0]}))
    twin = dict(s[0], settlement_id="other")
    assert any("two authoritative" in p for p in BS.verify(s + [twin], {"w1": ws[0]}))


def test_bankrolls_reconcile_to_the_cent_and_exclude_superseded_runs():
    ws = [w(1, stake=110.0), w(2, side="away", line=3.0, stake=37.0, bettor=1), w(3, stake=10.0, game="g2"),
          w(4, stake=999.0, run="old_m1")]                                                  # superseded run
    sets = BS.new_settlements(ws, {("nfl", "g1"): res(27, 20)}, [], "t")
    bettors = [{"population_id": POP, "bettor_index": 0, "archetype": "REC_CASUAL", "bankroll": 500.0},
               {"population_id": POP, "bettor_index": 1, "archetype": "SHARP", "bankroll": 1234.56}]
    rows, overdue = BS.bankrolls(bettors, ws, sets, {"r1"}, now=10**10, kickoffs={("nfl", "g2"): 1})
    b0 = next(r for r in rows if r["bettor_index"] == 0)
    assert b0["settled_pnl_cents"] == 10000 and b0["open_stake_cents"] == 1000              # +$100 win, $10 open
    assert b0["available_cents"] == 50000 + 10000 - 1000 and overdue == 1
    b1 = next(r for r in rows if r["bettor_index"] == 1)
    assert b1["available_cents"] == 123456 - 3700
    rec = BS.reconcile(ws, sets, {"r1"}, rows)
    assert rec["ok"] and rec["book_realized_cents"] == -rec["bettor_settled_pnl_cents"] == -10000 + 3700
    s = BS.summary(rows, ws, sets, {"r1"})["nfl"]
    assert s["settled_wagers"] == 2 and s["open_wagers"] == 1 and s["book_realized_pnl_cents"] == -6300


def test_settlement_never_writes_the_wager_ledger():
    import inspect
    src = inspect.getsource(BS)
    assert "INSERT" not in src and "UPDATE" not in src and "DELETE" not in src
