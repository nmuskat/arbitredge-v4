"""
Tests for features/run_slate_eligibility.py

Run with: python tests/test_run_slate_eligibility.py
"""

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features.run_slate_eligibility import (  # noqa: E402
    check_run_slate_eligibility, ELIGIBLE, NOT_ELIGIBLE_PRESEASON,
    AMBIGUOUS_CONFLICTING_TAGS, UNKNOWN_INSUFFICIENT_EVIDENCE,
)
from identity.seed_team_data import seed  # noqa: E402

TEST_DIR = Path(__file__).parent / "_run_slate_eligibility_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _make_game(conn, sport="nfl", home="nfl_kc", away="nfl_lac", ct="2026-09-13T20:00:00Z"):
    conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) VALUES (?,?,?,?)",
        (sport, home, away, ct),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _make_snapshot(conn, gid, sport, home, away, ct, observed_at="2026-09-12T00:00:00Z"):
    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, spread_home) VALUES (?,?,?,?,?, 'draftkings', ?, -3.0)",
        (gid, sport, home, away, ct, observed_at),
    )
    conn.commit()


def test_eligible_real_regular_season_tag_only():
    conn = fresh_conn()
    gid = _make_game(conn, sport="nfl")
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", "2026-09-13T20:00:00Z")

    result = check_run_slate_eligibility(conn, gid)
    assert result["status"] == ELIGIBLE
    assert result["has_regular_tag"] is True
    assert result["has_preseason_tag"] is False
    conn.close()
    print("PASS: a game with only real 'nfl' tagged snapshots is correctly ELIGIBLE")


def test_eligible_cfb():
    conn = fresh_conn()
    gid = _make_game(conn, sport="cfb", home="cfb_ohio_st", away="cfb_michigan")
    _make_snapshot(conn, gid, "cfb", "Ohio State", "Michigan", "2026-09-13T20:00:00Z")

    result = check_run_slate_eligibility(conn, gid)
    assert result["status"] == ELIGIBLE
    conn.close()
    print("PASS: CFB eligibility works identically, matched against its own 'cfb' identity sport")


def test_not_eligible_preseason_tag():
    conn = fresh_conn()
    gid = _make_game(conn, sport="nfl", ct="2026-08-15T20:00:00Z")
    _make_snapshot(conn, gid, "nfl_preseason", "KC", "LAC", "2026-08-15T20:00:00Z")

    result = check_run_slate_eligibility(conn, gid)
    assert result["status"] == NOT_ELIGIBLE_PRESEASON
    assert result["has_preseason_tag"] is True
    conn.close()
    print("PASS: a game with 'nfl_preseason' tagged snapshots is correctly NOT_ELIGIBLE_PRESEASON")


def test_ambiguous_conflicting_tags_fails_closed():
    conn = fresh_conn()
    gid = _make_game(conn, sport="nfl")
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", "2026-09-13T20:00:00Z", observed_at="2026-09-12T00:00:00Z")
    _make_snapshot(conn, gid, "nfl_preseason", "KC", "LAC", "2026-09-13T20:00:00Z", observed_at="2026-09-12T01:00:00Z")

    result = check_run_slate_eligibility(conn, gid)
    assert result["status"] == AMBIGUOUS_CONFLICTING_TAGS
    assert result["has_preseason_tag"] is True and result["has_regular_tag"] is True
    conn.close()
    print("PASS: conflicting 'nfl' + 'nfl_preseason' tags on the same game correctly fail closed "
          "as AMBIGUOUS_CONFLICTING_TAGS, never silently picking one")


def test_unknown_zero_market_snapshots():
    conn = fresh_conn()
    gid = _make_game(conn, sport="nfl")

    result = check_run_slate_eligibility(conn, gid)
    assert result["status"] == UNKNOWN_INSUFFICIENT_EVIDENCE
    assert result["has_preseason_tag"] is False and result["has_regular_tag"] is False
    conn.close()
    print("PASS: zero market_snapshots rows correctly produces UNKNOWN_INSUFFICIENT_EVIDENCE, "
          "not a false ELIGIBLE from mere absence of the preseason tag")


def test_unknown_nonexistent_game():
    conn = fresh_conn()
    result = check_run_slate_eligibility(conn, 99999)
    assert result["status"] == UNKNOWN_INSUFFICIENT_EVIDENCE
    conn.close()
    print("PASS: a nonexistent canonical_game_id correctly fails closed rather than raising or guessing")


def test_only_eligible_status_should_be_treated_as_safe():
    all_statuses = {ELIGIBLE, NOT_ELIGIBLE_PRESEASON, AMBIGUOUS_CONFLICTING_TAGS, UNKNOWN_INSUFFICIENT_EVIDENCE}
    safe_statuses = {s for s in all_statuses if s == ELIGIBLE}
    assert safe_statuses == {ELIGIBLE}
    assert len(all_statuses) == 4
    print("PASS: exactly one of the four possible statuses (ELIGIBLE) should ever be treated as safe")


if __name__ == "__main__":
    test_eligible_real_regular_season_tag_only()
    test_eligible_cfb()
    test_not_eligible_preseason_tag()
    test_ambiguous_conflicting_tags_fails_closed()
    test_unknown_zero_market_snapshots()
    test_unknown_nonexistent_game()
    test_only_eligible_status_should_be_treated_as_safe()
    print("\nAll RUN SLATE eligibility tests passed.")
