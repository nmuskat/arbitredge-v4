"""
Tests for run_slate/discover_slate.py and run_slate/orchestrate.py

Run with: python tests/test_run_slate_orchestration.py
"""

import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_slate.discover_slate import discover_upcoming_slate  # noqa: E402
from run_slate.orchestrate import run_slate  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402

TEST_DIR = Path(__file__).parent / "_run_slate_orchestration_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _make_game(conn, sport, home, away, ct):
    conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) VALUES (?,?,?,?)",
        (sport, home, away, ct),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _make_snapshot(conn, gid, sport, home, away, ct, book, observed_at, spread_home):
    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, spread_home) VALUES (?,?,?,?,?,?,?,?)",
        (gid, sport, home, away, ct, book, observed_at, spread_home),
    )
    conn.commit()


def _make_result(conn, gid, hs, aws, season_type, retrieved):
    wh = 1 if hs > aws else 0
    conn.execute(
        "INSERT INTO game_results (canonical_game_id, observed_at, game_status, is_final, "
        "final_score_home, final_score_away, winner_home, season_year, season_type, source, retrieved_at) "
        "VALUES (?,?, 'STATUS_FINAL',1,?,?,?,2025,?, 'espn', ?)",
        (gid, retrieved, hs, aws, wh, season_type, retrieved),
    )
    conn.commit()


def _future(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _past(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def test_eligible_upcoming_nfl_game_produces_full_output():
    conn = fresh_conn()
    ct = _future(10)
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", ct)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", ct, "draftkings", _past(1), -3.0)

    result = run_slate(conn, "nfl", "2026-01-01", "2027-12-31")
    assert len(result["eligible_games"]) == 1
    game = result["eligible_games"][0]
    assert game["canonical_game_id"] == gid
    assert game["eligibility_status"] == "ELIGIBLE"
    assert "v2_features" in game
    assert "market_state_by_book" in game
    assert "draftkings" in game["market_state_by_book"]
    conn.close()
    print("PASS: an eligible upcoming NFL game produces complete factual output")


def test_preseason_game_excluded():
    conn = fresh_conn()
    ct = _future(5)
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", ct)
    _make_snapshot(conn, gid, "nfl_preseason", "KC", "LAC", ct, "draftkings", _past(1), -3.0)

    result = run_slate(conn, "nfl", "2026-01-01", "2027-12-31")
    assert len(result["eligible_games"]) == 0
    assert len(result["excluded_games"]) == 1
    assert result["excluded_games"][0]["status"] == "NOT_ELIGIBLE_PRESEASON"
    conn.close()
    print("PASS: a preseason-tagged game is correctly excluded, never entering eligible_games")


def test_conflicting_tags_excluded():
    conn = fresh_conn()
    ct = _future(5)
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", ct)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", ct, "draftkings", _past(1), -3.0)
    _make_snapshot(conn, gid, "nfl_preseason", "KC", "LAC", ct, "draftkings", _past(1), -3.0)

    result = run_slate(conn, "nfl", "2026-01-01", "2027-12-31")
    assert len(result["eligible_games"]) == 0
    assert result["excluded_games"][0]["status"] == "AMBIGUOUS_CONFLICTING_TAGS"
    conn.close()
    print("PASS: a game with conflicting sport tags is correctly excluded, never silently resolved")


def test_unknown_missing_tag_excluded():
    conn = fresh_conn()
    ct = _future(5)
    _make_game(conn, "nfl", "nfl_kc", "nfl_lac", ct)

    result = run_slate(conn, "nfl", "2026-01-01", "2027-12-31")
    assert len(result["eligible_games"]) == 0
    assert result["excluded_games"][0]["status"] == "UNKNOWN_INSUFFICIENT_EVIDENCE"
    conn.close()
    print("PASS: a game with zero market_snapshots is correctly excluded as UNKNOWN, not assumed eligible")


def test_no_result_yet_game_successfully_computes():
    conn = fresh_conn()
    ct = _future(10)
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", ct)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", ct, "draftkings", _past(1), -3.0)

    result = run_slate(conn, "nfl", "2026-01-01", "2027-12-31")
    assert len(result["eligible_games"]) == 1
    assert result["eligible_games"][0]["v2_features"] is not None
    conn.close()
    print("PASS: a game with NO confirmed result yet still successfully computes real V2 features")


def test_future_observations_excluded_from_historical_as_of():
    conn = fresh_conn()
    ct = "2026-09-20T20:00:00Z"
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", ct)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", ct, "draftkings", "2026-09-10T00:00:00Z", -2.0)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", ct, "draftkings", "2026-09-19T00:00:00Z", -10.0)

    historical_as_of = "2026-09-12T00:00:00Z"
    result = run_slate(conn, "nfl", "2026-09-01", "2026-09-30", as_of=historical_as_of)
    game = result["eligible_games"][0]
    state = game["market_state_by_book"]["draftkings"]
    assert state["home_spread"] == -2.0
    conn.close()
    print("PASS: an observation made AFTER the requested as_of never leaks backward")


def test_per_book_independence():
    conn = fresh_conn()
    ct = _future(10)
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", ct)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", ct, "draftkings", _past(1), -3.0)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", ct, "fanduel", _past(1), -2.5)

    result = run_slate(conn, "nfl", "2026-01-01", "2027-12-31")
    state = result["eligible_games"][0]["market_state_by_book"]
    assert state["draftkings"]["home_spread"] == -3.0
    assert state["fanduel"]["home_spread"] == -2.5
    conn.close()
    print("PASS: different books' market state resolves independently, never collapsed into one value")


def test_missing_market_and_context_data_handled_gracefully():
    conn = fresh_conn()
    ct = _future(10)
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", ct)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", ct, "draftkings", _past(1), -3.0)

    result = run_slate(conn, "nfl", "2026-01-01", "2027-12-31")
    game = result["eligible_games"][0]
    assert game["v2_features"]["ranking_home"] is None
    assert game["v2_features"]["tv_network"] is None
    conn.close()
    print("PASS: missing context data is handled gracefully -- None fields, never a crash or fabrication")


def test_deterministic_replay_with_explicit_as_of():
    conn = fresh_conn()
    ct = _future(10)
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", ct)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", ct, "draftkings", _past(1), -3.0)

    explicit_as_of = _past(0)
    result1 = run_slate(conn, "nfl", "2026-01-01", "2027-12-31", as_of=explicit_as_of)
    result2 = run_slate(conn, "nfl", "2026-01-01", "2027-12-31", as_of=explicit_as_of)
    assert result1["eligible_games"] == result2["eligible_games"]
    conn.close()
    print("PASS: an explicit as_of timestamp produces fully deterministic, reproducible output across replays")


def test_no_writes_to_any_table():
    conn = fresh_conn()
    ct = _future(10)
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", ct)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", ct, "draftkings", _past(1), -3.0)
    other_gid = _make_game(conn, "nfl", "nfl_gb", "nfl_chi", "2026-01-04T20:00:00Z")
    _make_result(conn, other_gid, 30, 10, 2, "2026-01-05T00:00:00Z")

    tables = ["engineered_features", "engineered_features_v2", "market_state_by_cutoff",
              "market_snapshots", "game_results", "game_context"]
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

    run_slate(conn, "nfl", "2026-01-01", "2027-12-31")

    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    assert before == after, f"RUN SLATE wrote to a table it must never touch: before={before} after={after}"
    conn.close()
    print("PASS: RUN SLATE writes to ZERO tables -- confirmed across V1, V2, market state, raw collection, "
          "results, and context tables simultaneously")


def test_discover_upcoming_slate_excludes_past_games():
    conn = fresh_conn()
    _make_game(conn, "nfl", "nfl_kc", "nfl_lac", _past(5))
    future_gid = _make_game(conn, "nfl", "nfl_sea", "nfl_dal", _future(10))

    discovered = discover_upcoming_slate(conn, "nfl", "2026-01-01", "2027-01-01")
    discovered_ids = {d[0] for d in discovered}
    assert future_gid in discovered_ids
    assert len(discovered) == 1
    conn.close()
    print("PASS: slate discovery correctly excludes already-past games, only returns genuinely upcoming ones")


def test_invalid_sport_raises():
    conn = fresh_conn()
    try:
        discover_upcoming_slate(conn, "nba", "2026-01-01", "2027-12-31")
        assert False
    except ValueError:
        pass
    conn.close()
    print("PASS: an unsupported sport value is rejected explicitly")


if __name__ == "__main__":
    test_eligible_upcoming_nfl_game_produces_full_output()
    test_preseason_game_excluded()
    test_conflicting_tags_excluded()
    test_unknown_missing_tag_excluded()
    test_no_result_yet_game_successfully_computes()
    test_future_observations_excluded_from_historical_as_of()
    test_per_book_independence()
    test_missing_market_and_context_data_handled_gracefully()
    test_deterministic_replay_with_explicit_as_of()
    test_no_writes_to_any_table()
    test_discover_upcoming_slate_excludes_past_games()
    test_invalid_sport_raises()
    print("\nAll RUN SLATE orchestration tests passed.")
