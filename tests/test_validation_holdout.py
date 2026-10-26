"""
Tests for validation/harness/holdout.py

Run with: python tests/test_validation_holdout.py
"""

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.harness.holdout import (  # noqa: E402
    create_holdout, get_holdout_game_ids, filter_out_holdout_games,
    access_holdout_for_confirmation, HoldoutAlreadyAccessedError,
)
from identity.seed_team_data import seed  # noqa: E402

TEST_DIR = Path(__file__).parent / "_validation_holdout_test_workspace"


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
    conn.execute("INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) VALUES (?,?,?,?)",
                 (sport, home, away, ct))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_create_holdout_and_membership():
    conn = fresh_conn()
    g1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-12-01T20:00:00Z")
    g2 = _make_game(conn, "nfl", "nfl_kc", "nfl_den", "2025-12-08T20:00:00Z")

    status = create_holdout(conn, "mechanical_diagnostic_holdout_2025", [g1, g2])
    assert status == "created"
    members = set(get_holdout_game_ids(conn, "mechanical_diagnostic_holdout_2025"))
    assert members == {g1, g2}
    conn.close()
    print("PASS: create_holdout correctly locks membership")


def test_create_holdout_is_idempotent():
    conn = fresh_conn()
    g1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-12-01T20:00:00Z")
    create_holdout(conn, "hk", [g1])
    status2 = create_holdout(conn, "hk", [g1, 999])
    assert status2 == "already_exists"
    members = get_holdout_game_ids(conn, "hk")
    assert members == [g1]
    conn.close()
    print("PASS: create_holdout is idempotent -- a second call never silently changes locked membership")


def test_filter_out_holdout_games():
    conn = fresh_conn()
    g1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-12-01T20:00:00Z")
    g2 = _make_game(conn, "nfl", "nfl_kc", "nfl_den", "2025-12-08T20:00:00Z")
    g3 = _make_game(conn, "nfl", "nfl_kc", "nfl_lv", "2025-09-01T20:00:00Z")
    create_holdout(conn, "hk", [g1, g2])

    filtered = filter_out_holdout_games(conn, "hk", [g1, g2, g3])
    assert filtered == [g3]
    conn.close()
    print("PASS: filter_out_holdout_games correctly excludes locked holdout games from a candidate list")


def test_first_access_succeeds_second_access_refused():
    conn = fresh_conn()
    g1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-12-01T20:00:00Z")
    create_holdout(conn, "hk", [g1])

    result1 = access_holdout_for_confirmation(conn, "hk", experiment_id=None)
    assert result1 == [g1]

    try:
        access_holdout_for_confirmation(conn, "hk", experiment_id=None)
        assert False, "second access must raise"
    except HoldoutAlreadyAccessedError:
        pass

    access_count = conn.execute(
        "SELECT COUNT(*) FROM validation_holdout_access_log WHERE holdout_key='hk'"
    ).fetchone()[0]
    assert access_count == 2
    conn.close()
    print("PASS: first holdout access succeeds, second access is refused, and BOTH attempts are logged")


def test_repeated_refused_access_keeps_being_refused():
    conn = fresh_conn()
    g1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-12-01T20:00:00Z")
    create_holdout(conn, "hk", [g1])
    access_holdout_for_confirmation(conn, "hk")

    for _ in range(3):
        try:
            access_holdout_for_confirmation(conn, "hk")
            assert False
        except HoldoutAlreadyAccessedError:
            pass

    access_count = conn.execute(
        "SELECT COUNT(*) FROM validation_holdout_access_log WHERE holdout_key='hk'"
    ).fetchone()[0]
    assert access_count == 4
    conn.close()
    print("PASS: every repeated access attempt after the first keeps being refused, and every attempt is logged")


def test_two_different_holdout_keys_are_fully_independent():
    conn = fresh_conn()
    g1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-12-01T20:00:00Z")
    g2 = _make_game(conn, "nfl", "nfl_kc", "nfl_den", "2026-12-01T20:00:00Z")
    create_holdout(conn, "mechanical_diagnostic_holdout_2025", [g1])
    create_holdout(conn, "real_behavioral_holdout_2026", [g2])

    result_mechanical = access_holdout_for_confirmation(conn, "mechanical_diagnostic_holdout_2025")
    assert result_mechanical == [g1]

    result_behavioral = access_holdout_for_confirmation(conn, "real_behavioral_holdout_2026")
    assert result_behavioral == [g2]

    try:
        access_holdout_for_confirmation(conn, "mechanical_diagnostic_holdout_2025")
        assert False
    except HoldoutAlreadyAccessedError:
        pass
    conn.close()
    print("PASS: two differently-keyed holdouts are fully independent -- "
          "accessing one never consumes or interferes with the other")


def test_append_only_triggers_on_holdout_tables():
    conn = fresh_conn()
    g1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-12-01T20:00:00Z")
    create_holdout(conn, "hk", [g1])

    try:
        conn.execute("UPDATE validation_holdout_membership SET canonical_game_id=999 WHERE holdout_key='hk'")
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised

    try:
        conn.execute("DELETE FROM validation_holdout_membership WHERE holdout_key='hk'")
        conn.commit()
        raised_delete = False
    except sqlite3.IntegrityError:
        raised_delete = True
    assert raised_delete
    conn.close()
    print("PASS: validation_holdout_membership append-only triggers actually block UPDATE and DELETE")


if __name__ == "__main__":
    test_create_holdout_and_membership()
    test_create_holdout_is_idempotent()
    test_filter_out_holdout_games()
    test_first_access_succeeds_second_access_refused()
    test_repeated_refused_access_keeps_being_refused()
    test_two_different_holdout_keys_are_fully_independent()
    test_append_only_triggers_on_holdout_tables()
    print("\nAll validation holdout tests passed.")
