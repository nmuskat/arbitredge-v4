"""
Tests for features/build_engineered_features_v1.py (integration level --
pure logic is already covered by tests/test_compute_v1.py).

Run with: python tests/test_build_engineered_features_v1.py
"""

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features.build_engineered_features_v1 import (  # noqa: E402
    get_cohort, get_latest_results_by_canonical_game, get_all_games,
    build_team_index, get_prior_games, build_feature_row, write_feature_row,
    FEATURE_SET_VERSION,
)
from identity.seed_team_data import seed  # noqa: E402

TEST_DIR = Path(__file__).parent / "_build_features_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _make_game(conn, sport, home, away, commence_time):
    conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) VALUES (?,?,?,?)",
        (sport, home, away, commence_time),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _make_result(conn, canonical_game_id, home_score, away_score, season_year=2025, retrieved_at="2026-01-01T00:00:00Z"):
    winner_home = 1 if home_score > away_score else (0 if away_score > home_score else None)
    conn.execute(
        "INSERT INTO game_results (canonical_game_id, observed_at, game_status, is_final, "
        "final_score_home, final_score_away, winner_home, season_year, season_type, "
        "source, retrieved_at) VALUES (?, ?, 'STATUS_FINAL', 1, ?, ?, ?, ?, 2, 'espn', ?)",
        (canonical_game_id, retrieved_at, home_score, away_score, winner_home, season_year, retrieved_at),
    )
    conn.commit()


def test_get_prior_games_excludes_future_and_same_time():
    conn = fresh_conn()
    g1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-09-07T20:00:00Z")
    _make_result(conn, g1, 24, 17)
    g2 = _make_game(conn, "nfl", "nfl_kc", "nfl_den", "2025-09-14T20:00:00Z")
    _make_result(conn, g2, 30, 10)

    all_games = get_all_games(conn)
    latest_results = get_latest_results_by_canonical_game(conn)
    team_index = build_team_index(all_games, latest_results)

    prior = get_prior_games(team_index, "nfl_kc", "2025-09-07T20:00:00Z")
    assert prior == [], "no prior games should exist before the very first game -- future g2 must not leak backward"

    prior2 = get_prior_games(team_index, "nfl_kc", "2025-09-14T20:00:00Z")
    assert len(prior2) == 1
    assert prior2[0]["commence_time"] == "2025-09-07T20:00:00Z"
    conn.close()
    print("PASS: get_prior_games correctly excludes future and same-instant games at the DB-query level")


def test_end_to_end_feature_row_written_correctly():
    conn = fresh_conn()
    g1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-09-07T20:00:00Z")
    _make_result(conn, g1, 24, 17)
    g2 = _make_game(conn, "nfl", "nfl_kc", "nfl_den", "2025-09-14T20:00:00Z")
    _make_result(conn, g2, 30, 10)

    all_games = get_all_games(conn)
    latest_results = get_latest_results_by_canonical_game(conn)
    team_index = build_team_index(all_games, latest_results)

    row = build_feature_row(team_index, all_games, g2, "nfl", "nfl_kc", "nfl_den", "2025-09-14T20:00:00Z", latest_results)
    row["feature_set_version"] = FEATURE_SET_VERSION
    row["computed_at"] = "2026-01-01T00:00:00Z"
    row["point_in_time_cutoff"] = "2025-09-14T20:00:00Z"

    outcome = write_feature_row(conn, row)
    assert outcome == "new"

    stored = conn.execute(
        "SELECT home_games_played_before_this, home_wins_last_3, home_last_game_result, "
        "away_games_played_before_this, ranking_home, tv_network, favorite_side "
        "FROM engineered_features WHERE canonical_game_id = ?",
        (g2,),
    ).fetchone()
    assert stored[0] == 1
    assert stored[1] == 1
    assert stored[2] == "W"
    assert stored[3] == 0
    assert stored[4] is None and stored[5] is None and stored[6] is None
    conn.close()
    print("PASS: an end-to-end feature row is written correctly, with gated fields left NULL")


def test_idempotent_rerun_does_not_duplicate():
    conn = fresh_conn()
    g1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-09-07T20:00:00Z")
    _make_result(conn, g1, 24, 17)

    all_games = get_all_games(conn)
    latest_results = get_latest_results_by_canonical_game(conn)
    team_index = build_team_index(all_games, latest_results)

    row1 = build_feature_row(team_index, all_games, g1, "nfl", "nfl_kc", "nfl_lac", "2025-09-07T20:00:00Z", latest_results)
    row1.update({"feature_set_version": FEATURE_SET_VERSION, "computed_at": "2026-01-01T00:00:00Z",
                 "point_in_time_cutoff": "2025-09-07T20:00:00Z"})
    outcome1 = write_feature_row(conn, row1)

    row2 = build_feature_row(team_index, all_games, g1, "nfl", "nfl_kc", "nfl_lac", "2025-09-07T20:00:00Z", latest_results)
    row2.update({"feature_set_version": FEATURE_SET_VERSION, "computed_at": "2026-01-02T00:00:00Z",
                 "point_in_time_cutoff": "2025-09-07T20:00:00Z"})
    outcome2 = write_feature_row(conn, row2)

    assert outcome1 == "new"
    assert outcome2 == "unchanged"
    count = conn.execute(
        "SELECT COUNT(*) FROM engineered_features WHERE canonical_game_id = ? AND feature_set_version = ?",
        (g1, FEATURE_SET_VERSION),
    ).fetchone()[0]
    assert count == 1
    conn.close()
    print("PASS: rerunning the build for the same feature_set_version is idempotent, no duplicate rows")


def test_different_version_creates_new_row_not_overwrite():
    conn = fresh_conn()
    g1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-09-07T20:00:00Z")
    _make_result(conn, g1, 24, 17)

    all_games = get_all_games(conn)
    latest_results = get_latest_results_by_canonical_game(conn)
    team_index = build_team_index(all_games, latest_results)

    row_v1 = build_feature_row(team_index, all_games, g1, "nfl", "nfl_kc", "nfl_lac", "2025-09-07T20:00:00Z", latest_results)
    row_v1.update({"feature_set_version": "v1", "computed_at": "2026-01-01T00:00:00Z",
                   "point_in_time_cutoff": "2025-09-07T20:00:00Z"})
    write_feature_row(conn, row_v1)

    row_v2 = build_feature_row(team_index, all_games, g1, "nfl", "nfl_kc", "nfl_lac", "2025-09-07T20:00:00Z", latest_results)
    row_v2.update({"feature_set_version": "v2", "computed_at": "2026-01-02T00:00:00Z",
                   "point_in_time_cutoff": "2025-09-07T20:00:00Z"})
    outcome = write_feature_row(conn, row_v2)

    assert outcome == "new"
    versions = conn.execute(
        "SELECT feature_set_version FROM engineered_features WHERE canonical_game_id = ? ORDER BY feature_set_version",
        (g1,),
    ).fetchall()
    assert [v[0] for v in versions] == ["v1", "v2"]
    conn.close()
    print("PASS: a new feature_set_version creates a new row and preserves the old version, never overwrites")


def test_engineered_features_append_only_triggers_work():
    conn = fresh_conn()
    g1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-09-07T20:00:00Z")
    _make_result(conn, g1, 24, 17)
    all_games = get_all_games(conn)
    latest_results = get_latest_results_by_canonical_game(conn)
    team_index = build_team_index(all_games, latest_results)
    row = build_feature_row(team_index, all_games, g1, "nfl", "nfl_kc", "nfl_lac", "2025-09-07T20:00:00Z", latest_results)
    row.update({"feature_set_version": "v1", "computed_at": "2026-01-01T00:00:00Z",
                "point_in_time_cutoff": "2025-09-07T20:00:00Z"})
    write_feature_row(conn, row)

    try:
        conn.execute("UPDATE engineered_features SET home_wins_last_3 = 99 WHERE canonical_game_id = ?", (g1,))
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "engineered_features must be append-only -- UPDATE should be blocked by the DB trigger"

    try:
        conn.execute("DELETE FROM engineered_features WHERE canonical_game_id = ?", (g1,))
        conn.commit()
        raised_delete = False
    except sqlite3.IntegrityError:
        raised_delete = True
    assert raised_delete
    conn.close()
    print("PASS: engineered_features append-only triggers actually block UPDATE and DELETE")


def test_get_cohort_uses_fbs_allowlist_and_season_window():
    conn = fresh_conn()
    g_nfl = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-09-07T20:00:00Z")
    _make_result(conn, g_nfl, 24, 17)
    g_cfb_fbs = _make_game(conn, "cfb", "cfb_ohio_st", "cfb_michigan", "2025-11-29T17:00:00Z")
    _make_result(conn, g_cfb_fbs, 30, 24)
    g_cfb_fcs = _make_game(conn, "cfb", "cfb_ohio_st", "cfb_montana_state", "2025-09-06T17:00:00Z")
    _make_result(conn, g_cfb_fcs, 45, 3)
    g_out_of_window = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2024-09-07T20:00:00Z")
    _make_result(conn, g_out_of_window, 20, 14)

    from collectors.run_results_backfill_dryrun import load_fbs_allowlist
    allowlist = load_fbs_allowlist(2025)
    cohort = get_cohort(conn, allowlist)
    cohort_ids = {c[0] for c in cohort}

    assert g_nfl in cohort_ids
    assert g_cfb_fbs in cohort_ids
    assert g_cfb_fcs not in cohort_ids, "FBS-vs-FCS game must be excluded from the cohort"
    assert g_out_of_window not in cohort_ids, "a game outside the 2025 season window must be excluded"
    conn.close()
    print("PASS: get_cohort correctly applies both the FBS allowlist and the season-window filter")


def test_preseason_game_never_enters_v1_cohort_even_within_date_window():
    """The exact regression this safeguard exists for: a preseason game
    (season_type=1) whose commence_time falls inside the same Aug-Feb
    date window as the regular season must NEVER enter get_cohort()'s
    output, regardless of date range overlap."""
    conn = fresh_conn()
    g_regular = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-09-07T20:00:00Z")
    _make_result(conn, g_regular, 24, 17)  # season_type=2 via the shared _make_result helper

    g_preseason = _make_game(conn, "nfl", "nfl_kc", "nfl_den", "2025-08-09T20:00:00Z")
    conn.execute(
        "INSERT INTO game_results (canonical_game_id, observed_at, game_status, is_final, "
        "final_score_home, final_score_away, winner_home, season_year, season_type, "
        "source, retrieved_at) VALUES (?, '2025-08-09T23:00:00Z', 'STATUS_FINAL', 1, ?, ?, ?, 2025, 1, "
        "'espn', '2025-08-09T23:00:00Z')",
        (g_preseason, 20, 13, 1),
    )
    conn.commit()

    from collectors.run_results_backfill_dryrun import load_fbs_allowlist
    allowlist = load_fbs_allowlist(2025)
    cohort = get_cohort(conn, allowlist)
    cohort_ids = {c[0] for c in cohort}

    assert g_regular in cohort_ids, "the real regular-season game must still be included"
    assert g_preseason not in cohort_ids, (
        "a preseason game (season_type=1) must NEVER enter the V1 cohort, "
        "even though its commence_time falls inside the same date window"
    )
    conn.close()
    print("PASS: a preseason game is correctly excluded from the V1 cohort by season_type, "
          "even when its date falls inside the regular-season window (the exact pooling risk flagged)")


if __name__ == "__main__":
    test_get_prior_games_excludes_future_and_same_time()
    test_end_to_end_feature_row_written_correctly()
    test_idempotent_rerun_does_not_duplicate()
    test_different_version_creates_new_row_not_overwrite()
    test_engineered_features_append_only_triggers_work()
    test_get_cohort_uses_fbs_allowlist_and_season_window()
    test_preseason_game_never_enters_v1_cohort_even_within_date_window()
    print("\nAll build_engineered_features_v1 integration tests passed.")
