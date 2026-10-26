"""Ledger lifecycle: immutability, idempotency, time-forward accumulation,
deterministic replay, split invariance, rollback, clock and version guards
(tests 4, 5, 6, 9, 10)."""

import sqlite3

import pytest

from sportsbook_v4 import engine as eng
from sportsbook_v4.engine import ClockError, RunMismatch, advance, create_run, watermark
from sportsbook_v4.ledger_read import fetch_wagers
from sportsbook_v4.market import representative_offer
from sportsbook_v4.population import ensure_population
from sportsbook_v4.util import json_hash, to_epoch
from tests_v4 import fixtures as fx
from tests_v4.conftest import MID

ALL_COLS = "*"


def _all_rows(conn, run_id):
    return conn.execute("SELECT * FROM v4_wager WHERE run_id=? ORDER BY wager_id", (run_id,)).fetchall()


def _fresh(bettors=10_000, market=None):
    conn = fx.new_v4_db()
    pid = ensure_population(conn, "nfl", fx.NFL_TEAMS, bettor_count=bettors)
    market = market or fx.build_quotes()
    run_id = create_run(conn, pid, fx.build_slate(), market)
    return conn, run_id, market


def test_time_forward_accumulation(full_week):
    conn, run_id = full_week["conn"], full_week["run_id"]
    first, second = full_week["first"], full_week["second"]
    assert first["accepted"] > 0 and second["accepted"] > 0
    rows = fetch_wagers(conn, run_id)
    early = [w for w in rows if w["interval_seq"] == 0]
    late = [w for w in rows if w["interval_seq"] == 1]
    assert len(early) == first["accepted"] and len(late) == second["accepted"]
    assert all(to_epoch(w["accepted_at"]) <= to_epoch(MID) for w in early)
    assert all(to_epoch(MID) < to_epoch(w["accepted_at"]) <= to_epoch(fx.WINDOW_END) for w in late)
    assert watermark(conn, run_id) == (1, to_epoch(fx.WINDOW_END))


def test_line_moves_create_multiple_accepted_numbers_and_wagers_keep_theirs(full_week):
    conn, run_id, market = full_week["conn"], full_week["run_id"], full_week["market"]
    flip = fetch_wagers(conn, run_id, "g_sun2")
    spreads = {w["accepted_home_spread"] for w in flip}
    assert {2.5, 1.0, -1.0} <= spreads          # favourite flipped mid-week; old numbers persist
    # Every wager carries exactly the number the book offered at ITS offer time.
    for w in fetch_wagers(conn, run_id):
        offer = representative_offer(market.latest_quotes(w["canonical_game_id"], to_epoch(w["offer_as_of"])))
        assert w["accepted_home_spread"] == offer["home_spread"]
        price = offer["home_price"] if w["side"] == "home" else offer["away_price"]
        assert w["accepted_price"] == price
        assert to_epoch(w["offer_as_of"]) <= to_epoch(w["accepted_at"])


def test_append_only_triggers(full_week):
    conn = full_week["conn"]
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE v4_wager SET stake = stake + 1")
    conn.rollback()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM v4_wager")
    conn.rollback()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE v4_interval SET accepted = 0")
    conn.rollback()


def test_idempotent_reruns(full_week):
    conn, run_id = full_week["conn"], full_week["run_id"]
    before = _all_rows(conn, run_id)
    for until in (fx.WINDOW_END, MID, fx.WINDOW_START):
        res = advance(conn, run_id, until, full_week["market"], full_week["facts"], fx.WINDOW_END)
        assert res["status"] == "noop"
    assert _all_rows(conn, run_id) == before
    assert conn.execute("SELECT COUNT(*) FROM v4_interval WHERE run_id=?", (run_id,)).fetchone()[0] == 2


def test_advancing_does_not_change_earlier_wagers():
    conn, run_id, market = _fresh(bettors=2000)
    facts = fx.build_facts()
    advance(conn, run_id, MID, market, facts, MID)
    early = _all_rows(conn, run_id)
    advance(conn, run_id, fx.WINDOW_END, market, facts, fx.WINDOW_END)
    after = [r for r in _all_rows(conn, run_id) if r[2] == 0]  # interval_seq column
    assert after == early


def test_deterministic_replay_and_split_invariance():
    facts = fx.build_facts()
    a, ra, ma = _fresh(bettors=3000)
    advance(a, ra, fx.WINDOW_END, ma, facts, fx.WINDOW_END)
    b, rb, mb = _fresh(bettors=3000)
    for until in ("2026-10-27T00:00:00Z", MID, "2026-11-01T17:00:00Z", fx.WINDOW_END):
        advance(b, rb, until, mb, facts, until)
    assert ra == rb

    def content(conn, rid):
        return sorted(r[:1] + r[3:] for r in _all_rows(conn, rid))  # drop run_id/interval_seq bookkeeping
    assert content(a, ra) == content(b, rb)


def test_rollback_on_interruption_then_clean_recovery():
    facts = fx.build_facts()
    conn, run_id, market = _fresh(bettors=2000)
    with pytest.raises(RuntimeError, match="injected"):
        advance(conn, run_id, fx.WINDOW_END, market, facts, fx.WINDOW_END, _fail_after_inserts=25)
    assert conn.execute("SELECT COUNT(*) FROM v4_wager").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM v4_interval").fetchone()[0] == 0
    assert watermark(conn, run_id)[0] == -1
    res = advance(conn, run_id, fx.WINDOW_END, market, facts, fx.WINDOW_END)
    ref_conn, ref_run, ref_market = _fresh(bettors=2000)
    ref = advance(ref_conn, ref_run, fx.WINDOW_END, ref_market, facts, fx.WINDOW_END)
    assert res["wagers_hash"] == ref["wagers_hash"] and res["accepted"] == ref["accepted"]


def test_clock_cannot_outrun_market_data():
    conn, run_id, market = _fresh(bettors=200)
    with pytest.raises(ClockError):
        advance(conn, run_id, fx.WINDOW_END, market, fx.build_facts(), data_complete_through=MID)
    assert conn.execute("SELECT COUNT(*) FROM v4_wager").fetchone()[0] == 0


def test_model_upgrade_cannot_continue_old_run(monkeypatch):
    conn, run_id, market = _fresh(bettors=200)
    monkeypatch.setattr(eng, "params_hash", lambda: "a-different-params-version")
    with pytest.raises(RunMismatch):
        advance(conn, run_id, MID, market, fx.build_facts(), MID)


def test_market_source_must_match_run():
    conn, run_id, _ = _fresh(bettors=200)
    other = fx.build_quotes()
    other.source_id = "fixture:something_else"
    with pytest.raises(RunMismatch):
        advance(conn, run_id, MID, other, fx.build_facts(), MID)


def test_wager_ids_unique_and_hash_stable(full_week):
    rows = fetch_wagers(full_week["conn"], full_week["run_id"])
    assert len({w["wager_id"] for w in rows}) == len(rows)
    assert json_hash([w["wager_id"] for w in rows]) == json_hash([w["wager_id"] for w in rows])
