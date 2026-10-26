"""Isolation, leakage, decision separation, prospective immutability,
read-only dashboard access, production-DB guard (tests 11, 12, 14, 15, 16)."""

import ast
import hashlib
import sqlite3
from pathlib import Path

import pytest

from sportsbook_v4.decision import (
    FinancialNeed, MarketConfirmation, assess_game, financial_need_from_exposure, market_confirmation,
)
from sportsbook_v4.engine import RunMismatch, advance, create_run, load_run
from sportsbook_v4.facts import CircularInputError, FixtureFacts
from sportsbook_v4.ledger_read import exposure_timeline, fetch_wagers, game_book, open_readonly
from sportsbook_v4.market import FixtureMarket, SnapshotMarket, quote
from sportsbook_v4.population import ensure_population
from sportsbook_v4.prospective import ImmutableRecordConflict, NotProspective, record_decision
from sportsbook_v4.schema import ProductionDatabaseGuard, ensure_schema
from sportsbook_v4.util import to_epoch
from tests_v4 import fixtures as fx
from tests_v4.conftest import MID

PKG = Path(__file__).resolve().parent.parent / "sportsbook_v4"
FORBIDDEN_IMPORT_ROOTS = {"book_need", "behavioral", "web", "operations", "collectors", "simulation"}


# ------------------------------------------------------------ isolation (11)

def test_v4_package_imports_nothing_from_legacy_or_production_paths():
    for f in PKG.glob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert n.split(".")[0] not in FORBIDDEN_IMPORT_ROOTS, f"{f.name} imports {n}"


def test_sport_and_population_isolation():
    conn = fx.new_v4_db()
    nfl = ensure_population(conn, "nfl", fx.NFL_TEAMS, bettor_count=300)
    cfb = ensure_population(conn, "cfb", ["cfb_a", "cfb_b"], bettor_count=300)
    assert nfl != cfb
    uids = conn.execute("SELECT population_id, bettor_uid FROM v4_bettor").fetchall()
    nfl_uids = {u for p, u in uids if p == nfl}
    cfb_uids = {u for p, u in uids if p == cfb}
    assert nfl_uids.isdisjoint(cfb_uids)
    with pytest.raises(RunMismatch):
        create_run(conn, cfb, fx.build_slate(), fx.build_quotes())   # NFL slate on CFB population
    market = fx.build_quotes()
    r1 = create_run(conn, nfl, fx.build_slate(), market)
    advance(conn, r1, MID, market, fx.build_facts(), MID)
    assert all(w["canonical_game_id"].startswith("g_") for w in fetch_wagers(conn, r1))
    assert conn.execute("SELECT COUNT(*) FROM v4_wager WHERE population_id=?", (cfb,)).fetchone()[0] == 0


def test_population_is_idempotent_and_verified():
    conn = fx.new_v4_db()
    a = ensure_population(conn, "nfl", fx.NFL_TEAMS, bettor_count=500)
    b = ensure_population(conn, "nfl", list(reversed(fx.NFL_TEAMS)), bettor_count=500)
    assert a == b
    assert conn.execute("SELECT COUNT(*) FROM v4_bettor").fetchone()[0] == 500


def test_refuses_to_write_into_a_market_database(tmp_path):
    conn = sqlite3.connect(tmp_path / "looks_like_prod.db")
    conn.execute("CREATE TABLE market_snapshots (snapshot_id INTEGER)")
    with pytest.raises(ProductionDatabaseGuard):
        ensure_schema(conn)


# ------------------------------------------------------------- leakage (12)

def test_future_market_data_cannot_change_past_wagers():
    """Run the same week twice: once with the real path, once with an extra
    wild quote injected AFTER T. Every wager accepted at or before T must be
    identical (the run identity is held fixed so seeds match)."""
    T = "2026-10-28T12:00:00Z"
    base = fx.build_quotes()
    altered_quotes = {g: list(qs) for g, qs in base.quotes.items()}
    for book in fx.BOOKS:
        altered_quotes["g_snf"].append(quote(book, "2026-10-28T12:30:00Z", -14.0, -200, +170, ref=f"future:{book}"))
    altered = FixtureMarket(altered_quotes, source_id=base.source_id)

    def run(market):
        conn = fx.new_v4_db()
        pid = ensure_population(conn, "nfl", fx.NFL_TEAMS, bettor_count=3000)
        rid = create_run(conn, pid, fx.build_slate(), market)
        advance(conn, rid, fx.WINDOW_END, market, fx.build_facts(), fx.WINDOW_END)
        return [w for w in fetch_wagers(conn, rid) if to_epoch(w["accepted_at"]) <= to_epoch(T)], fetch_wagers(conn, rid)

    past_a, all_a = run(base)
    past_b, all_b = run(altered)
    assert past_a == past_b and len(past_a) > 0
    assert all_a != all_b           # sanity: the future change did matter later


def test_market_lookup_never_returns_future_quotes():
    m = fx.build_quotes()
    t = to_epoch("2026-10-27T15:30:00Z")
    for q in m.latest_quotes("g_tnf", t):
        assert q.effective_epoch <= t
    # retail books still lag the 15:00 reference move at 15:30
    by_book = {q.book: q.home_spread for q in m.latest_quotes("g_tnf", t)}
    assert by_book["lowvig"] == -3.0 and by_book["draftkings"] == -3.5


def test_forbidden_model_fields_rejected():
    with pytest.raises(CircularInputError):
        FixtureFacts({"g": [(0, {"public_side": "home"})]})
    with pytest.raises(CircularInputError):
        FixtureFacts({"g": [(0, {"financial_need_side": "away"})]})


def test_snapshot_adapter_is_read_only_and_point_in_time(tmp_path):
    path = tmp_path / "market.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE market_snapshots (snapshot_id INTEGER PRIMARY KEY, canonical_game_id TEXT, book TEXT, "
              "observed_at TEXT, book_last_update TEXT, spread_home REAL, spread_away REAL, "
              "spread_price_home REAL, spread_price_away REAL)")
    c.executemany("INSERT INTO market_snapshots VALUES (?,?,?,?,?,?,?,?,?)", [
        (1, "g", "dk", "2026-10-26T10:00:00Z", "2026-10-26T09:00:00Z", -3.0, 3.0, -110, -110),
        (2, "g", "dk", "2026-10-27T10:00:00Z", None, -3.5, 3.5, -105, -115),
        (3, "g", "fd", "2026-10-26T10:00:00Z", None, -2.5, 2.5, -110, -110),
    ])
    c.commit()
    c.close()
    sm = SnapshotMarket(path)
    early = {q.book: q for q in sm.latest_quotes("g", to_epoch("2026-10-26T09:30:00Z"))}
    assert set(early) == {"dk"} and early["dk"].ref == "snapshot:1"   # book_last_update governs
    late = {q.book: q.home_spread for q in sm.latest_quotes("g", to_epoch("2026-10-28T00:00:00Z"))}
    assert late == {"dk": -3.5, "fd": -2.5}
    with pytest.raises(sqlite3.OperationalError):
        sm.conn.execute("INSERT INTO market_snapshots (snapshot_id) VALUES (99)")
    sm.close()


# --------------------------------------------------------- separation (14)

def _need(side, swing=50000.0, tickets=1000):
    return FinancialNeed("DIRECTIONAL" if side else "TIE", side, 1.0, 2.0, swing, -5.0, tickets, 1e5, "t", "r")


def test_financial_need_survives_contradicting_market():
    need = _need("away")
    conf = MarketConfirmation("CONTRADICTS", "v", -3.0, -4.0, -1.0, None, {"legacy_regime": "CONFIRMATION"})
    out = assess_game(need, conf)
    assert out["financial_need"] is need and out["financial_need"].side == "away"
    assert out["official_play"].decision == "PASS"
    assert "MARKET_CONTRADICTS" in out["official_play"].reasons


def test_official_play_can_pass_while_need_exists():
    out = assess_game(_need("home"), MarketConfirmation("CONFIRMS", "v", -3.0, -4.0, 1.0, None))
    assert out["financial_need"].side == "home"
    assert out["official_play"].decision == "PASS"
    assert out["official_play"].candidate_rule_would_play is True


def test_market_confirmation_direction():
    m = fx.build_quotes()
    open_t, now_t = to_epoch(fx.WINDOW_START), to_epoch("2026-10-31T00:00:00Z")
    # g_sun1: KC -9.5 -> -10.5 (home got more expensive)
    assert market_confirmation("home", m, "g_sun1", open_t, now_t).status == "CONFIRMS"
    assert market_confirmation("away", m, "g_sun1", open_t, now_t).status == "CONTRADICTS"
    assert market_confirmation(None, m, "g_sun1", open_t, now_t).status == "UNAVAILABLE"
    assert market_confirmation("home", m, "g_sun3", open_t, now_t).status == "NEUTRAL"


# ---------------------------------------------------- prospective (15)

def test_prospective_records_are_immutable(full_week):
    conn = fx.new_v4_db()
    run = load_run(full_week["conn"], full_week["run_id"])
    game = next(g for g in run["slate"].games if g.canonical_game_id == "g_snf")
    exp = game_book(full_week["conn"], full_week["run_id"], "g_snf", as_of="2026-11-02T00:20:00Z")
    need = financial_need_from_exposure(exp, "2026-11-02T00:20:00Z", full_week["run_id"])
    conf = market_confirmation(need.side, full_week["market"], "g_snf", to_epoch(fx.WINDOW_START),
                               to_epoch("2026-11-02T00:20:00Z"))
    assessment = assess_game(need, conf)
    kwargs = dict(game=game, sport="nfl", decision_point="T-60m", decided_at="2026-11-02T00:20:00Z",
                  run_id=full_week["run_id"], ledger_watermark="2026-11-02T00:20:00Z", assessment=assessment)
    did, status = record_decision(conn, **kwargs)
    assert status == "written"
    assert record_decision(conn, **kwargs) == (did, "unchanged")
    other = assess_game(_need("home" if need.side == "away" else "away"), conf)
    with pytest.raises(ImmutableRecordConflict):
        record_decision(conn, **dict(kwargs, assessment=other))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE v4_prospective_decision SET official_play='PLAY'")
    conn.rollback()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM v4_prospective_decision")
    conn.rollback()
    with pytest.raises(NotProspective):
        record_decision(conn, **dict(kwargs, decision_point="late", decided_at=game.commence_time))


# ---------------------------------------------------- dashboard reads (16)

def test_dashboard_reads_have_no_side_effects(full_week):
    path = full_week["path"]
    full_week["conn"].commit()
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    ro = open_readonly(path)
    before = ro.execute("SELECT COUNT(*) FROM v4_wager").fetchone()[0]
    for _ in range(3):  # "refresh" the dashboard a few times
        for g in full_week["slate"].games:
            game_book(ro, full_week["run_id"], g.canonical_game_id, as_of="2026-10-31T00:00:00Z")
        exposure_timeline(ro, full_week["run_id"], "g_tnf")
    assert ro.total_changes == 0
    assert ro.execute("SELECT COUNT(*) FROM v4_wager").fetchone()[0] == before
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("DELETE FROM v4_interval")
    ro.close()
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest


def test_book_as_of_only_counts_wagers_accepted_by_then(full_week):
    ro = open_readonly(full_week["path"])
    early = game_book(ro, full_week["run_id"], "g_snf", as_of=MID)
    late = game_book(ro, full_week["run_id"], "g_snf")
    assert 0 < early["tickets"] < late["tickets"]
    tl = exposure_timeline(ro, full_week["run_id"], "g_snf")
    assert [p["tickets"] for p in tl] == sorted(p["tickets"] for p in tl)
    ro.close()
