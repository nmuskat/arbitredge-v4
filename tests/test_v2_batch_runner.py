"""
Tests for features/build_engineered_features_v2_batch.py

Run with: python tests/test_v2_batch_runner.py
"""

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features.build_engineered_features_v2_batch import (  # noqa: E402
    get_nfl_regular_cohort_v2, get_cfb_regular_cohort_v2, check_2026_fbs_membership_available,
    run_batch, NAMED_CUTOFFS,
)
from identity.seed_team_data import seed  # noqa: E402
import collectors.run_results_backfill_dryrun as fbs_module  # noqa: E402

TEST_DIR = Path(__file__).parent / "_v2_batch_test_workspace"


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


def _make_result(conn, gid, hs, aws, season_type=2, retrieved="2026-08-07T00:00:00Z"):
    wh = 1 if hs > aws else 0
    conn.execute(
        "INSERT INTO game_results (canonical_game_id, observed_at, game_status, is_final, "
        "final_score_home, final_score_away, winner_home, season_year, season_type, source, retrieved_at) "
        "VALUES (?,?, 'STATUS_FINAL',1,?,?,?,2026,?, 'espn', ?)",
        (gid, retrieved, hs, aws, wh, season_type, retrieved),
    )
    conn.commit()


def _make_snapshot(conn, gid, sport, home, away, ct, book, observed_at, spread_home):
    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, spread_home) VALUES (?,?,?,?,?,?,?,?)",
        (gid, sport, home, away, ct, book, observed_at, spread_home),
    )
    conn.commit()


def _write_temp_fbs_csv(rows):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, dir=str(TEST_DIR))
    f.write("season,canonical_team_id,classification,source,source_note\n")
    for season, team_id in rows:
        f.write(f"{season},{team_id},FBS,test,test\n")
    f.close()
    return Path(f.name)


def test_nfl_cohort_excludes_preseason_and_unconfirmed_games():
    conn = fresh_conn()
    completed = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2026-08-06T20:00:00Z")
    _make_result(conn, completed, 24, 17, season_type=2)

    preseason = _make_game(conn, "nfl", "nfl_sea", "nfl_dal", "2026-08-16T00:00:00Z")
    _make_snapshot(conn, preseason, "nfl_preseason", "SEA", "DAL", "2026-08-16T00:00:00Z",
                    "draftkings", "2026-08-15T20:00:00Z", -3.0)
    _make_result(conn, preseason, 20, 13, season_type=1)

    no_result_yet = _make_game(conn, "nfl", "nfl_gb", "nfl_chi", "2026-12-01T20:00:00Z")

    cohort = get_nfl_regular_cohort_v2(conn)
    cohort_ids = {c[0] for c in cohort}

    assert completed in cohort_ids
    assert preseason not in cohort_ids
    assert no_result_yet not in cohort_ids
    conn.close()
    print("PASS: NFL cohort correctly includes only confirmed regular-season games, "
          "excluding preseason-tagged and result-pending games")


def test_cfb_refused_entirely_with_no_2026_fbs_data():
    conn = fresh_conn()
    gid = _make_game(conn, "cfb", "cfb_ohio_st", "cfb_michigan", "2026-08-06T20:00:00Z")
    _make_result(conn, gid, 31, 20, season_type=2)

    empty_2025_only = _write_temp_fbs_csv([(2025, "cfb_ohio_st"), (2025, "cfb_michigan")])
    original_path = fbs_module.FBS_MEMBERSHIP_PATH
    fbs_module.FBS_MEMBERSHIP_PATH = empty_2025_only
    try:
        assert check_2026_fbs_membership_available() is False
        results = run_batch(conn, feature_set_version="v2")
    finally:
        fbs_module.FBS_MEMBERSHIP_PATH = original_path

    v2_count = conn.execute(
        "SELECT COUNT(*) FROM engineered_features_v2 WHERE canonical_game_id=?", (gid,)
    ).fetchone()[0]
    assert v2_count == 0
    assert results["features_new"] == 0
    conn.close()
    print("PASS: CFB processing is refused entirely (zero persistence) when only 2025 FBS data exists -- "
          "the hard prerequisite is enforced, no silent 2025 fallback")


def test_cfb_processes_when_real_2026_fbs_data_exists():
    conn = fresh_conn()
    gid = _make_game(conn, "cfb", "cfb_ohio_st", "cfb_michigan", "2026-08-06T20:00:00Z")
    _make_snapshot(conn, gid, "cfb", "Ohio State", "Michigan", "2026-08-06T20:00:00Z",
                    "draftkings", "2026-08-05T12:00:00Z", -7.0)
    _make_result(conn, gid, 31, 20, season_type=2)

    real_2026 = _write_temp_fbs_csv([(2026, "cfb_ohio_st"), (2026, "cfb_michigan")])
    original_path = fbs_module.FBS_MEMBERSHIP_PATH
    fbs_module.FBS_MEMBERSHIP_PATH = real_2026
    try:
        assert check_2026_fbs_membership_available() is True
        results = run_batch(conn, feature_set_version="v2")
    finally:
        fbs_module.FBS_MEMBERSHIP_PATH = original_path

    v2_count = conn.execute(
        "SELECT COUNT(*) FROM engineered_features_v2 WHERE canonical_game_id=?", (gid,)
    ).fetchone()[0]
    assert v2_count == len(NAMED_CUTOFFS)
    assert results["features_new"] >= 1
    conn.close()
    print("PASS: CFB processes normally once real, verified 2026 FBS membership data exists")


def test_cfb_excludes_non_fbs_matchup_even_with_real_2026_data():
    conn = fresh_conn()
    gid = _make_game(conn, "cfb", "cfb_ohio_st", "cfb_montana_state", "2026-08-06T20:00:00Z")
    _make_result(conn, gid, 45, 3, season_type=2)

    real_2026 = _write_temp_fbs_csv([(2026, "cfb_ohio_st")])
    original_path = fbs_module.FBS_MEMBERSHIP_PATH
    fbs_module.FBS_MEMBERSHIP_PATH = real_2026
    try:
        cohort = get_cfb_regular_cohort_v2(conn, {"cfb_ohio_st"})
    finally:
        fbs_module.FBS_MEMBERSHIP_PATH = original_path

    assert gid not in {c[0] for c in cohort}
    conn.close()
    print("PASS: a non-FBS-vs-FBS matchup is correctly excluded even when real 2026 data exists")


def test_batch_is_idempotent_across_reruns():
    conn = fresh_conn()
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2026-08-06T20:00:00Z")
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", "2026-08-06T20:00:00Z", "draftkings", "2026-08-05T12:00:00Z", -3.0)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", "2026-08-06T20:00:00Z", "fanduel", "2026-08-05T12:00:00Z", -2.5)
    _make_result(conn, gid, 24, 17, season_type=2)

    first = run_batch(conn, feature_set_version="v2")
    second = run_batch(conn, feature_set_version="v2")

    assert first["features_new"] == len(NAMED_CUTOFFS)
    assert second["features_new"] == 0
    assert second["features_unchanged"] == len(NAMED_CUTOFFS)
    assert first["market_state_new"] == second["market_state_unchanged"]

    v2_count = conn.execute(
        "SELECT COUNT(*) FROM engineered_features_v2 WHERE canonical_game_id=?", (gid,)
    ).fetchone()[0]
    ms_count = conn.execute(
        "SELECT COUNT(*) FROM market_state_by_cutoff WHERE canonical_game_id=?", (gid,)
    ).fetchone()[0]
    assert v2_count == len(NAMED_CUTOFFS)
    assert ms_count == len(NAMED_CUTOFFS) * 2
    conn.close()
    print("PASS: the batch runner is fully idempotent across reruns -- no duplicate rows in either V2 table")


def test_batch_never_persists_a_post_kickoff_cutoff():
    conn = fresh_conn()
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2026-08-06T20:00:00Z")
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", "2026-08-06T20:00:00Z", "draftkings", "2026-08-05T12:00:00Z", -3.0)
    _make_result(conn, gid, 24, 17, season_type=2)

    run_batch(conn, feature_set_version="v2")

    rows = conn.execute(
        "SELECT point_in_time_cutoff FROM engineered_features_v2 WHERE canonical_game_id=?", (gid,)
    ).fetchall()
    for (cutoff,) in rows:
        assert cutoff <= "2026-08-06T20:00:00Z"
    conn.close()
    print("PASS: every persisted cutoff is at or before the game's real kickoff, never after")


def test_batch_never_touches_v1():
    conn = fresh_conn()
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2026-08-06T20:00:00Z")
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", "2026-08-06T20:00:00Z", "draftkings", "2026-08-05T12:00:00Z", -3.0)
    _make_result(conn, gid, 24, 17, season_type=2)

    v1_count_before = conn.execute("SELECT COUNT(*) FROM engineered_features").fetchone()[0]
    run_batch(conn, feature_set_version="v2")
    v1_count_after = conn.execute("SELECT COUNT(*) FROM engineered_features").fetchone()[0]

    assert v1_count_before == v1_count_after == 0
    conn.close()
    print("PASS: the V2 batch runner never writes to the V1 engineered_features table")


if __name__ == "__main__":
    test_nfl_cohort_excludes_preseason_and_unconfirmed_games()
    test_cfb_refused_entirely_with_no_2026_fbs_data()
    test_cfb_processes_when_real_2026_fbs_data_exists()
    test_cfb_excludes_non_fbs_matchup_even_with_real_2026_data()
    test_batch_is_idempotent_across_reruns()
    test_batch_never_persists_a_post_kickoff_cutoff()
    test_batch_never_touches_v1()
    print("\nAll V2 batch runner tests passed.")
