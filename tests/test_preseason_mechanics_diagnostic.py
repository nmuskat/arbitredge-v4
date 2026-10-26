"""
Tests for behavioral/run_preseason_mechanics_diagnostic.py

Run with: python tests/test_preseason_mechanics_diagnostic.py
"""

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from behavioral.run_preseason_mechanics_diagnostic import get_regular_and_postseason_results_only  # noqa: E402
from features.build_engineered_features_v1 import (  # noqa: E402
    get_all_games, build_team_index, get_prior_games, get_latest_results_by_canonical_game,
)
from identity.seed_team_data import seed  # noqa: E402

TEST_DIR = Path(__file__).parent / "_preseason_diagnostic_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _make_game(conn, home, away, ct):
    conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) VALUES ('nfl',?,?,?)",
        (home, away, ct),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _make_result(conn, gid, hs, aws, season_type, retrieved):
    wh = 1 if hs > aws else 0
    conn.execute(
        "INSERT INTO game_results (canonical_game_id, observed_at, game_status, is_final, "
        "final_score_home, final_score_away, winner_home, season_year, season_type, source, retrieved_at) "
        "VALUES (?,?, 'STATUS_FINAL',1,?,?,?,2025,?, 'espn', ?)",
        (gid, retrieved, hs, aws, wh, season_type, retrieved),
    )
    conn.commit()


def test_preseason_results_excluded_from_recency_history():
    conn = fresh_conn()
    regular_game = _make_game(conn, "nfl_kc", "nfl_lac", "2026-01-04T20:00:00Z")
    _make_result(conn, regular_game, 30, 10, season_type=2, retrieved="2026-01-05T00:00:00Z")

    preseason_game = _make_game(conn, "nfl_kc", "nfl_sf", "2026-08-08T20:00:00Z")
    _make_result(conn, preseason_game, 3, 24, season_type=1, retrieved="2026-08-09T00:00:00Z")

    all_games = get_all_games(conn)
    filtered_results = get_regular_and_postseason_results_only(conn)
    filtered_index = build_team_index(all_games, filtered_results)
    kc_history = get_prior_games(filtered_index, "nfl_kc", "2026-08-15T20:00:00Z")

    assert len(kc_history) == 1
    assert kc_history[0]["result"] == "W" and kc_history[0]["points_for"] == 30
    conn.close()
    print("PASS: a preseason result is correctly excluded from recency history while a genuine "
          "regular-season result still carries forward across the season boundary")


def test_postseason_results_still_carry_forward():
    conn = fresh_conn()
    postseason_game = _make_game(conn, "nfl_kc", "nfl_buf", "2026-01-20T20:00:00Z")
    _make_result(conn, postseason_game, 27, 24, season_type=3, retrieved="2026-01-21T00:00:00Z")

    all_games = get_all_games(conn)
    filtered_results = get_regular_and_postseason_results_only(conn)
    filtered_index = build_team_index(all_games, filtered_results)
    kc_history = get_prior_games(filtered_index, "nfl_kc", "2026-08-15T20:00:00Z")

    assert len(kc_history) == 1
    conn.close()
    print("PASS: postseason results still carry forward correctly -- only preseason is excluded")


def test_unfiltered_v1_function_still_pools_preseason_unchanged():
    conn = fresh_conn()
    regular_game = _make_game(conn, "nfl_kc", "nfl_lac", "2026-01-04T20:00:00Z")
    _make_result(conn, regular_game, 30, 10, season_type=2, retrieved="2026-01-05T00:00:00Z")
    preseason_game = _make_game(conn, "nfl_kc", "nfl_sf", "2026-08-08T20:00:00Z")
    _make_result(conn, preseason_game, 3, 24, season_type=1, retrieved="2026-08-09T00:00:00Z")

    all_games = get_all_games(conn)
    unfiltered_index = build_team_index(all_games, get_latest_results_by_canonical_game(conn))
    kc_history = get_prior_games(unfiltered_index, "nfl_kc", "2026-08-15T20:00:00Z")

    assert len(kc_history) == 2, (
        "V1's own unmodified function must still pool preseason and regular season together"
    )
    conn.close()
    print("PASS: V1's original get_latest_results_by_canonical_game() is completely unmodified, "
          "still pooling all game types -- the fix is entirely additive, not a V1 change")


if __name__ == "__main__":
    test_preseason_results_excluded_from_recency_history()
    test_postseason_results_still_carry_forward()
    test_unfiltered_v1_function_still_pools_preseason_unchanged()
    print("\nAll preseason mechanics diagnostic tests passed.")
