"""
Tests for validation/feature_sets/behavioral_handle_intensity_v1.py and
validation/feature_sets/behavioral_side_preference_v1.py

Run with: python tests/test_behavioral_feature_sets.py
"""

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.feature_sets.behavioral_handle_intensity_v1 import BehavioralHandleIntensityV1FeatureSet  # noqa: E402
from validation.feature_sets.behavioral_side_preference_v1 import BehavioralSidePreferenceV1FeatureSet  # noqa: E402
from behavioral.handle_intensity_v1 import compute_handle_intensity_v1  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402

TEST_DIR = Path(__file__).parent / "_behavioral_feature_sets_test_workspace"


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


def _make_v2_row(conn, gid, cutoff, standalone_same=1, standalone_all=1, competing_same=0, competing_all=0,
                  home_wins3=1, away_wins3=1, rest_advantage=0):
    conn.execute(
        "INSERT INTO engineered_features_v2 (canonical_game_id, feature_set_version, computed_at, "
        "point_in_time_cutoff, is_standalone_same_sport, is_standalone_all_sports, "
        "competing_games_same_window_same_sport, competing_games_same_window_all_sports, "
        "home_wins_last_3, away_wins_last_3, rest_advantage_days) "
        "VALUES (?, 'v2', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (gid, cutoff, cutoff, standalone_same, standalone_all, competing_same, competing_all,
         home_wins3, away_wins3, rest_advantage),
    )
    conn.commit()


def test_returns_none_without_matching_v2_row():
    conn = fresh_conn()
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2026-09-13T20:00:00Z")
    hi_fs = BehavioralHandleIntensityV1FeatureSet()
    result = hi_fs.get_features(conn, gid, "2026-09-13T20:00:00Z")
    assert result is None
    conn.close()
    print("PASS: with no matching engineered_features_v2 row, get_features correctly returns None")


def test_point_in_time_integrity_exact_cutoff_match_required():
    conn = fresh_conn()
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2026-09-13T20:00:00Z")
    _make_v2_row(conn, gid, "2026-09-13T20:00:00Z")

    hi_fs = BehavioralHandleIntensityV1FeatureSet()
    result_wrong_cutoff = hi_fs.get_features(conn, gid, "2026-09-12T20:00:00Z")
    result_right_cutoff = hi_fs.get_features(conn, gid, "2026-09-13T20:00:00Z")

    assert result_wrong_cutoff is None
    assert result_right_cutoff is not None
    conn.close()
    print("PASS: point-in-time integrity enforced -- a mismatched cutoff correctly returns None "
          "rather than silently reusing data computed at a different cutoff")


def test_slate_resolution_finds_same_sport_same_window_games():
    conn = fresh_conn()
    gid1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2026-09-13T20:00:00Z")
    gid2 = _make_game(conn, "nfl", "nfl_sea", "nfl_dal", "2026-09-14T00:00:00Z")
    _make_v2_row(conn, gid1, "2026-09-13T20:00:00Z", competing_same=0)
    _make_v2_row(conn, gid2, "2026-09-14T00:00:00Z", competing_same=3)

    hi_fs = BehavioralHandleIntensityV1FeatureSet()
    result = hi_fs.get_features(conn, gid1, "2026-09-13T20:00:00Z")
    assert result["handle_intensity_slate_size"] == 2
    conn.close()
    print("PASS: slate resolution correctly finds another same-sport, same-cutoff-type game within the window")


def test_slate_resolution_excludes_different_sport():
    conn = fresh_conn()
    gid1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2026-09-13T20:00:00Z")
    gid2 = _make_game(conn, "cfb", "cfb_ohio_st", "cfb_michigan", "2026-09-13T20:00:00Z")
    _make_v2_row(conn, gid1, "2026-09-13T20:00:00Z")
    _make_v2_row(conn, gid2, "2026-09-13T20:00:00Z")

    hi_fs = BehavioralHandleIntensityV1FeatureSet()
    result = hi_fs.get_features(conn, gid1, "2026-09-13T20:00:00Z")
    assert result["handle_intensity_slate_size"] == 1
    conn.close()
    print("PASS: slate resolution correctly excludes a different-sport game even at the identical cutoff")


def test_slate_resolution_excludes_different_cutoff_type():
    conn = fresh_conn()
    gid1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2026-09-13T20:00:00Z")
    gid2 = _make_game(conn, "nfl", "nfl_sea", "nfl_dal", "2026-09-13T22:00:00Z")
    _make_v2_row(conn, gid1, "2026-09-13T20:00:00Z")
    _make_v2_row(conn, gid2, "2026-09-12T22:00:00Z")

    hi_fs = BehavioralHandleIntensityV1FeatureSet()
    result = hi_fs.get_features(conn, gid1, "2026-09-13T20:00:00Z")
    assert result["handle_intensity_slate_size"] == 1
    conn.close()
    print("PASS: games with different relative cutoff types are correctly excluded from each other's slate, "
          "even when temporally close")


def test_slate_resolution_excludes_outside_window():
    conn = fresh_conn()
    gid1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2026-09-13T20:00:00Z")
    gid2 = _make_game(conn, "nfl", "nfl_sea", "nfl_dal", "2026-09-25T20:00:00Z")
    _make_v2_row(conn, gid1, "2026-09-13T20:00:00Z")
    _make_v2_row(conn, gid2, "2026-09-25T20:00:00Z")

    hi_fs = BehavioralHandleIntensityV1FeatureSet()
    result = hi_fs.get_features(conn, gid1, "2026-09-13T20:00:00Z")
    assert result["handle_intensity_slate_size"] == 1
    conn.close()
    print("PASS: a game well outside the disclosed slate window is correctly excluded")


def test_side_preference_wrapper_returns_expected_shape():
    conn = fresh_conn()
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2026-09-13T20:00:00Z")
    _make_v2_row(conn, gid, "2026-09-13T20:00:00Z", home_wins3=3, away_wins3=0)

    sp_fs = BehavioralSidePreferenceV1FeatureSet()
    result = sp_fs.get_features(conn, gid, "2026-09-13T20:00:00Z")
    assert result is not None
    assert "side_preference_normalized_lean_score" in result
    assert "side_preference_direction" in result
    assert "EXPERIMENTAL" in result["side_preference_label"]
    conn.close()
    print("PASS: the Side Preference wrapper returns the expected shape with correct labeling")


def test_wrapper_output_matches_direct_frozen_model_call():
    conn = fresh_conn()
    gid1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2026-09-13T20:00:00Z")
    gid2 = _make_game(conn, "nfl", "nfl_sea", "nfl_dal", "2026-09-14T00:00:00Z")
    _make_v2_row(conn, gid1, "2026-09-13T20:00:00Z", competing_same=0)
    _make_v2_row(conn, gid2, "2026-09-14T00:00:00Z", competing_same=3)

    hi_fs = BehavioralHandleIntensityV1FeatureSet()
    wrapper_result = hi_fs.get_features(conn, gid1, "2026-09-13T20:00:00Z")

    direct_games = [
        {"canonical_game_id": gid1, "is_standalone_same_sport": 1, "is_standalone_all_sports": 1,
         "competing_games_same_window_same_sport": 0, "competing_games_same_window_all_sports": 0,
         "_context": {}},
        {"canonical_game_id": gid2, "is_standalone_same_sport": 1, "is_standalone_all_sports": 1,
         "competing_games_same_window_same_sport": 3, "competing_games_same_window_all_sports": 0,
         "_context": {}},
    ]
    direct_results = compute_handle_intensity_v1(direct_games)
    direct_target = next(r for r in direct_results if r["canonical_game_id"] == gid1)

    assert wrapper_result["handle_intensity_normalized_score"] == direct_target["normalized_score"]
    conn.close()
    print("PASS: the wrapper's output exactly matches a direct call to the unmodified frozen "
          "compute_handle_intensity_v1 on the identical slate -- proving no silent modification")


def test_point_in_time_cutoff_used_reports_correctly():
    conn = fresh_conn()
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2026-09-13T20:00:00Z")
    _make_v2_row(conn, gid, "2026-09-13T20:00:00Z")

    hi_fs = BehavioralHandleIntensityV1FeatureSet()
    assert hi_fs.point_in_time_cutoff_used(conn, gid) == "2026-09-13T20:00:00Z"
    assert hi_fs.point_in_time_cutoff_used(conn, 99999) is None
    conn.close()
    print("PASS: point_in_time_cutoff_used correctly reports the stored cutoff, None when absent")


if __name__ == "__main__":
    test_returns_none_without_matching_v2_row()
    test_point_in_time_integrity_exact_cutoff_match_required()
    test_slate_resolution_finds_same_sport_same_window_games()
    test_slate_resolution_excludes_different_sport()
    test_slate_resolution_excludes_different_cutoff_type()
    test_slate_resolution_excludes_outside_window()
    test_side_preference_wrapper_returns_expected_shape()
    test_wrapper_output_matches_direct_frozen_model_call()
    test_point_in_time_cutoff_used_reports_correctly()
    print("\nAll behavioral FeatureSet wrapper tests passed.")
