"""
Tests for operations/first_evidence_orchestrator.py

Run with: python tests/test_first_evidence_orchestrator.py
"""

import io
import shutil
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from identity.seed_team_data import seed  # noqa: E402

TEST_DIR = Path(__file__).parent / "_first_evidence_orchestrator_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn, db_path


def test_orchestrator_runs_clean_with_zero_evidence():
    conn, db_path = fresh_conn()
    conn.close()

    import operations.first_evidence_orchestrator as orch
    original_path = orch.DEFAULT_DB_PATH
    orch.DEFAULT_DB_PATH = db_path
    try:
        out = io.StringIO()
        with redirect_stdout(out):
            orch.main()
    finally:
        orch.DEFAULT_DB_PATH = original_path

    output = out.getvalue()
    assert "NFL confirmed regular-season games: 0" in output
    assert "not a readiness declaration" in output
    print("PASS: the orchestrator runs cleanly with zero real evidence, reporting honest zeros, "
          "never a fabricated readiness claim")


def test_orchestrator_reports_real_evidence_correctly():
    conn, db_path = fresh_conn()

    conn.execute("INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) VALUES "
                 "('nfl','nfl_kc','nfl_lac','2026-08-06T20:00:00Z')")
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for book, spread, obs in [("draftkings", -2.0, "2026-08-05T00:00:00Z"),
                                ("draftkings", -3.5, "2026-08-06T19:00:00Z"),
                                ("fanduel", -3.0, "2026-08-06T19:00:00Z")]:
        conn.execute(
            "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
            "book, observed_at, spread_home) VALUES (?, 'nfl', 'KC','LAC', '2026-08-06T20:00:00Z', ?, ?, ?)",
            (gid, book, obs, spread))
    conn.execute(
        "INSERT INTO game_results (canonical_game_id, observed_at, game_status, is_final, "
        "final_score_home, final_score_away, winner_home, season_year, season_type, source, retrieved_at) "
        "VALUES (?, '2026-08-07T00:00:00Z', 'STATUS_FINAL',1,24,17,1,2026,2, 'espn', '2026-08-07T00:00:00Z')", (gid,))
    conn.commit()
    conn.close()

    import operations.first_evidence_orchestrator as orch
    original_path = orch.DEFAULT_DB_PATH
    orch.DEFAULT_DB_PATH = db_path
    try:
        out = io.StringIO()
        with redirect_stdout(out):
            orch.main()
    finally:
        orch.DEFAULT_DB_PATH = original_path

    output = out.getvalue()
    assert "NFL confirmed regular-season games: 1" in output
    assert "CrossBookDisagreement: 1/1" in output
    assert "HandleIntensityV1 FeatureSet resolvable: 1/1" in output
    assert "SidePreferenceV1 FeatureSet resolvable: 1/1" in output
    print("PASS: the orchestrator correctly reports real evidence counts against a genuinely confirmed game")


def test_orchestrator_never_calls_behavioral_compute_functions():
    import inspect
    import operations.first_evidence_orchestrator as orch
    source = inspect.getsource(orch)
    assert "compute_handle_intensity_v1" not in source
    assert "compute_side_preference_v1" not in source
    print("PASS: the orchestrator never directly calls the behavioral compute functions -- "
          "it only checks FeatureSet resolvability through the harness wrapper, producing no score")


def test_orchestrator_never_writes_to_v1():
    conn, db_path = fresh_conn()
    conn.close()

    import operations.first_evidence_orchestrator as orch
    original_path = orch.DEFAULT_DB_PATH
    orch.DEFAULT_DB_PATH = db_path
    conn = sqlite3.connect(str(db_path))
    before = conn.execute("SELECT COUNT(*) FROM engineered_features").fetchone()[0]
    conn.close()
    try:
        out = io.StringIO()
        with redirect_stdout(out):
            orch.main()
    finally:
        orch.DEFAULT_DB_PATH = original_path

    conn = sqlite3.connect(str(db_path))
    after = conn.execute("SELECT COUNT(*) FROM engineered_features").fetchone()[0]
    conn.close()
    assert before == after == 0
    print("PASS: the orchestrator never writes to V1's engineered_features table")


if __name__ == "__main__":
    test_orchestrator_runs_clean_with_zero_evidence()
    test_orchestrator_reports_real_evidence_correctly()
    test_orchestrator_never_calls_behavioral_compute_functions()
    test_orchestrator_never_writes_to_v1()
    print("\nAll first-evidence orchestrator tests passed.")
