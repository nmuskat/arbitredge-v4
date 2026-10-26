"""
Tests for diagnostics/evidence_readiness_monitor.py

Run with: python tests/test_evidence_readiness_monitor.py
"""

import io
import shutil
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from identity.seed_team_data import seed  # noqa: E402

TEST_DIR = Path(__file__).parent / "_evidence_readiness_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn, db_path


def test_monitor_runs_clean_with_zero_confirmed_games():
    conn, db_path = fresh_conn()
    conn.close()

    import diagnostics.evidence_readiness_monitor as m
    original_path = m.DEFAULT_DB_PATH
    m.DEFAULT_DB_PATH = db_path
    try:
        out = io.StringIO()
        with redirect_stdout(out):
            m.main()
    finally:
        m.DEFAULT_DB_PATH = original_path

    output = out.getvalue()
    assert "0 confirmed regular-season games" in output
    assert "does not declare whether accumulated evidence is sufficient" in output
    print("PASS: the monitor runs cleanly with zero confirmed games, reports 0 without crashing, "
          "and never declares a readiness conclusion")


def test_monitor_correctly_counts_and_computes_overlap():
    conn, db_path = fresh_conn()

    conn.execute("INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) VALUES "
                 "('nfl','nfl_kc','nfl_lac','2026-08-06T20:00:00Z')")
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    for book, spread, obs in [("draftkings", -2.0, "2026-08-05T00:00:00Z"),
                                ("draftkings", -3.5, "2026-08-06T19:00:00Z"),
                                ("fanduel", -2.5, "2026-08-06T19:00:00Z")]:
        conn.execute(
            "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
            "book, observed_at, spread_home) VALUES (?, 'nfl', 'KC','LAC', '2026-08-06T20:00:00Z', ?, ?, ?)",
            (gid, book, obs, spread))

    conn.execute(
        "INSERT INTO engineered_features_v2 (canonical_game_id, feature_set_version, computed_at, "
        "point_in_time_cutoff, is_standalone_same_sport, is_standalone_all_sports, "
        "competing_games_same_window_same_sport, competing_games_same_window_all_sports, "
        "home_wins_last_3, away_wins_last_3, rest_advantage_days) "
        "VALUES (?, 'v2', '2026-08-06T20:00:00Z', '2026-08-06T20:00:00Z', 1, 1, 0, 0, 2, 1, 1)", (gid,))

    conn.execute(
        "INSERT INTO game_results (canonical_game_id, observed_at, game_status, is_final, "
        "final_score_home, final_score_away, winner_home, season_year, season_type, source, retrieved_at) "
        "VALUES (?, '2026-08-07T00:00:00Z', 'STATUS_FINAL',1,24,17,1,2026,2, 'espn', '2026-08-07T00:00:00Z')", (gid,))
    conn.commit()
    conn.close()

    import diagnostics.evidence_readiness_monitor as m
    original_path = m.DEFAULT_DB_PATH
    m.DEFAULT_DB_PATH = db_path
    try:
        out = io.StringIO()
        with redirect_stdout(out):
            m.main()
    finally:
        m.DEFAULT_DB_PATH = original_path

    output = out.getvalue()
    assert "1 confirmed regular-season games" in output
    assert "Handle Intensity: valid=0/1" in output
    assert "Handle Intensity: valid=1/1" in output
    assert output.count("MovementDirection evidence available:    1/1") == 4
    assert output.count("OVERLAP -- Side Preference x MovementDirection:      0") == 3
    assert "OVERLAP -- Side Preference x MovementDirection:      1" in output
    print("PASS: the monitor correctly counts real confirmed games, correctly distinguishes cutoffs "
          "with vs without V2 data, and correctly computes overlap counts")


if __name__ == "__main__":
    test_monitor_runs_clean_with_zero_confirmed_games()
    test_monitor_correctly_counts_and_computes_overlap()
    print("\nAll evidence readiness monitor tests passed.")
