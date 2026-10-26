"""
Tests for features/compute_v1.py -- written FIRST, before the orchestration
script, per instruction. Look-ahead leakage tests come first in this file.

Run with: python tests/test_compute_v1.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features.compute_v1 import (  # noqa: E402
    compute_team_features, compute_slate_features, BLOWOUT_MARGIN_THRESHOLD,
)


def _game(commence_time, result, points_for, points_against, season_year=2025):
    return {
        "commence_time": commence_time, "result": result,
        "points_for": points_for, "points_against": points_against,
        "margin": points_for - points_against, "season_year": season_year,
    }


def test_future_game_in_history_raises_value_error():
    history = [
        _game("2025-10-20T20:00:00Z", "W", 30, 10),
    ]
    try:
        compute_team_features(history, target_commence_time="2025-10-13T20:00:00Z", target_season_year=2025)
        raised = False
    except ValueError as e:
        raised = True
        assert "Look-ahead leakage" in str(e)
    assert raised, "a history entry after the target commence_time must raise ValueError, not be silently used"
    print("PASS: a future game in the history list raises ValueError instead of leaking into features")


def test_game_at_exact_target_time_raises_value_error():
    history = [_game("2025-10-13T20:00:00Z", "W", 30, 10)]
    try:
        compute_team_features(history, target_commence_time="2025-10-13T20:00:00Z", target_season_year=2025)
        raised = False
    except ValueError:
        raised = True
    assert raised, "a history entry at exactly the target's own commence_time must be rejected (strict <, not <=)"
    print("PASS: a history entry at exactly the target's commence_time is rejected (strict inequality enforced)")


def test_only_prior_games_used_future_blowout_never_appears():
    correct_history = [
        _game("2025-10-06T20:00:00Z", "L", 10, 45),
    ]
    result = compute_team_features(correct_history, target_commence_time="2025-10-13T20:00:00Z", target_season_year=2025)
    assert result["last_game_result"] == "L"
    assert result["last_game_point_margin"] == -35
    leaked_history = correct_history + [_game("2025-10-20T20:00:00Z", "W", 45, 0)]
    try:
        compute_team_features(leaked_history, target_commence_time="2025-10-13T20:00:00Z", target_season_year=2025)
        assert False, "must have raised"
    except ValueError:
        pass
    print("PASS: correct point-in-time history produces sane output; a mistakenly-included future "
          "result is caught by the guard rather than silently changing the answer")


def test_zero_prior_games_all_null_except_support_fields():
    result = compute_team_features([], target_commence_time="2025-09-06T20:00:00Z", target_season_year=2025)
    assert result["games_played_before_this"] == 0
    assert result["last3_sample_size"] == 0
    assert result["last5_sample_size"] == 0
    assert result["current_season_games_before_this"] == 0
    for field in ["wins_last_3", "wins_last_5", "points_for_avg_last_3", "current_win_streak",
                  "days_since_last_game", "last_game_result", "last_game_blowout_win",
                  "last3_prior_season_games", "last_game_same_season"]:
        assert result[field] is None, f"{field} must be NULL for a season opener, got {result[field]!r}"
    print("PASS: zero prior games -- support fields are real zeros, all recency fields NULL, no fabrication")


def test_last3_last5_arithmetic():
    history = [
        _game("2025-10-06T20:00:00Z", "W", 30, 10),
        _game("2025-09-29T20:00:00Z", "L", 14, 21),
        _game("2025-09-22T20:00:00Z", "W", 24, 17),
        _game("2025-09-15T20:00:00Z", "W", 28, 7),
        _game("2025-09-08T20:00:00Z", "L", 10, 24),
    ]
    result = compute_team_features(history, target_commence_time="2025-10-13T20:00:00Z", target_season_year=2025)
    assert result["games_played_before_this"] == 5
    assert result["last3_sample_size"] == 3
    assert result["last5_sample_size"] == 5
    assert result["wins_last_3"] == 2
    assert result["wins_last_5"] == 3
    assert result["points_for_avg_last_3"] == (30 + 14 + 24) / 3
    assert result["points_against_avg_last_3"] == (10 + 21 + 17) / 3
    assert result["point_margin_avg_last_3"] == ((30 - 10) + (14 - 21) + (24 - 17)) / 3
    assert result["point_margin_avg_last_5"] == sum(g["margin"] for g in history[:5]) / 5
    print("PASS: last-3/last-5 win counts, scoring averages, and margin averages are arithmetically correct")


def test_partial_window_fewer_than_3_games():
    history = [
        _game("2025-09-15T20:00:00Z", "W", 28, 7),
        _game("2025-09-08T20:00:00Z", "L", 10, 24),
    ]
    result = compute_team_features(history, target_commence_time="2025-09-22T20:00:00Z", target_season_year=2025)
    assert result["games_played_before_this"] == 2
    assert result["last3_sample_size"] == 2
    assert result["wins_last_3"] == 1
    assert result["points_for_avg_last_3"] == (28 + 10) / 2
    print("PASS: fewer than 3 prior games computes over the real available count, with sample size disclosed")


def test_win_streak_and_loss_streak():
    history = [
        _game("2025-10-06T20:00:00Z", "W", 30, 10),
        _game("2025-09-29T20:00:00Z", "W", 24, 20),
        _game("2025-09-22T20:00:00Z", "W", 28, 14),
        _game("2025-09-15T20:00:00Z", "L", 10, 24),
    ]
    result = compute_team_features(history, target_commence_time="2025-10-13T20:00:00Z", target_season_year=2025)
    assert result["current_win_streak"] == 3
    assert result["current_loss_streak"] == 0
    print("PASS: win streak correctly stops counting at the first non-win, going most-recent-first")


def test_streak_broken_by_tie():
    history = [
        _game("2025-10-06T20:00:00Z", "T", 20, 20),
        _game("2025-09-29T20:00:00Z", "W", 24, 20),
    ]
    result = compute_team_features(history, target_commence_time="2025-10-13T20:00:00Z", target_season_year=2025)
    assert result["current_win_streak"] == 0, "a tie is not a win -- streak must reset to 0, not skip the tie"
    assert result["current_loss_streak"] == 0, "a tie is also not a loss"
    print("PASS: a tie correctly breaks both win and loss streak counting")


def test_days_since_last_game():
    history = [_game("2025-10-06T20:00:00Z", "W", 30, 10)]
    result = compute_team_features(history, target_commence_time="2025-10-13T20:00:00Z", target_season_year=2025)
    assert result["days_since_last_game"] == 7
    print("PASS: days_since_last_game computes the correct calendar gap")


def test_blowout_flag_threshold():
    win_history = [_game("2025-10-06T20:00:00Z", "W", 41, 20)]
    result = compute_team_features(win_history, target_commence_time="2025-10-13T20:00:00Z", target_season_year=2025)
    assert result["last_game_blowout_win"] == 1
    assert result["last_game_point_margin"] == BLOWOUT_MARGIN_THRESHOLD

    loss_history = [_game("2025-10-06T20:00:00Z", "L", 10, 31)]
    result2 = compute_team_features(loss_history, target_commence_time="2025-10-13T20:00:00Z", target_season_year=2025)
    assert result2["last_game_blowout_loss"] == 1

    close_history = [_game("2025-10-06T20:00:00Z", "W", 40, 20)]
    result3 = compute_team_features(close_history, target_commence_time="2025-10-13T20:00:00Z", target_season_year=2025)
    assert result3["last_game_blowout_win"] == 0
    print("PASS: blowout flag threshold (>=21) is applied correctly at and just below the boundary")


def test_all_current_season_no_carryover():
    history = [
        _game("2025-10-06T20:00:00Z", "W", 30, 10, season_year=2025),
        _game("2025-09-29T20:00:00Z", "L", 14, 21, season_year=2025),
    ]
    result = compute_team_features(history, target_commence_time="2025-10-13T20:00:00Z", target_season_year=2025)
    assert result["current_season_games_before_this"] == 2
    assert result["last3_prior_season_games"] == 0
    assert result["last_game_same_season"] == 1
    print("PASS: all-current-season history shows zero prior-season carryover")


def test_prior_season_carryover_2026_opener():
    history = [
        _game("2025-12-28T20:00:00Z", "W", 30, 10, season_year=2025),
        _game("2025-12-21T20:00:00Z", "L", 14, 21, season_year=2025),
        _game("2025-12-14T20:00:00Z", "W", 24, 17, season_year=2025),
    ]
    result = compute_team_features(history, target_commence_time="2026-09-05T20:00:00Z", target_season_year=2026)
    assert result["current_season_games_before_this"] == 0
    assert result["last3_prior_season_games"] == 3
    assert result["last_game_same_season"] == 0
    assert result["games_played_before_this"] == 3
    print("PASS: a 2026 opener transparently shows all-prior-season carryover in last-3, "
          "without excluding the data or applying any decay weight")


def test_mixed_season_carryover():
    history = [
        _game("2026-09-05T20:00:00Z", "W", 24, 10, season_year=2026),
        _game("2025-12-28T20:00:00Z", "L", 14, 21, season_year=2025),
        _game("2025-12-21T20:00:00Z", "W", 30, 17, season_year=2025),
    ]
    result = compute_team_features(history, target_commence_time="2026-09-12T20:00:00Z", target_season_year=2026)
    assert result["current_season_games_before_this"] == 1
    assert result["last3_prior_season_games"] == 2
    assert result["last_game_same_season"] == 1
    print("PASS: mixed current-season/prior-season history in the last-3 window is transparently disclosed")


def test_standalone_game_zero_competitors():
    all_games = [
        {"canonical_game_id": 1, "sport": "nfl", "commence_time": "2025-11-27T18:00:00Z"},
        {"canonical_game_id": 2, "sport": "nfl", "commence_time": "2025-11-27T21:00:00Z"},
    ]
    result = compute_slate_features(all_games, target_canonical_game_id=1, target_sport="nfl",
                                     target_commence_time="2025-11-27T18:00:00Z")
    assert result["competing_games_same_window_same_sport"] == 0
    assert result["is_standalone_same_sport"] == 1
    assert result["is_standalone_all_sports"] == 1
    print("PASS: a game with no competitors within the window is correctly classified standalone")


def test_competing_games_within_window_counted():
    all_games = [
        {"canonical_game_id": 1, "sport": "nfl", "commence_time": "2025-11-27T18:00:00Z"},
        {"canonical_game_id": 2, "sport": "nfl", "commence_time": "2025-11-27T18:30:00Z"},
        {"canonical_game_id": 3, "sport": "cfb", "commence_time": "2025-11-27T18:45:00Z"},
        {"canonical_game_id": 4, "sport": "nfl", "commence_time": "2025-11-27T21:00:00Z"},
    ]
    result = compute_slate_features(all_games, target_canonical_game_id=1, target_sport="nfl",
                                     target_commence_time="2025-11-27T18:00:00Z")
    assert result["competing_games_same_window_same_sport"] == 1
    assert result["competing_games_same_window_all_sports"] == 2
    assert result["is_standalone_same_sport"] == 0
    assert result["is_standalone_all_sports"] == 0
    print("PASS: competing-games counts correctly split same-sport vs all-sports within the +/-60min window")


def test_window_boundary_exact_edge():
    all_games = [
        {"canonical_game_id": 1, "sport": "nfl", "commence_time": "2025-11-27T18:00:00Z"},
        {"canonical_game_id": 2, "sport": "nfl", "commence_time": "2025-11-27T19:00:00Z"},
        {"canonical_game_id": 3, "sport": "nfl", "commence_time": "2025-11-27T19:00:01Z"},
    ]
    result = compute_slate_features(all_games, target_canonical_game_id=1, target_sport="nfl",
                                     target_commence_time="2025-11-27T18:00:00Z")
    assert result["competing_games_same_window_same_sport"] == 1, "exactly +60min is inclusive"
    print("PASS: the +/-60 minute window boundary is handled correctly at the exact edge")


def test_day_of_week_derivation():
    all_games = [{"canonical_game_id": 1, "sport": "nfl", "commence_time": "2025-11-27T18:00:00Z"}]
    result = compute_slate_features(all_games, target_canonical_game_id=1, target_sport="nfl",
                                     target_commence_time="2025-11-27T18:00:00Z")
    assert result["day_of_week"] == "Thursday"
    print("PASS: day_of_week is correctly derived from commence_time")


if __name__ == "__main__":
    test_future_game_in_history_raises_value_error()
    test_game_at_exact_target_time_raises_value_error()
    test_only_prior_games_used_future_blowout_never_appears()
    test_zero_prior_games_all_null_except_support_fields()
    test_last3_last5_arithmetic()
    test_partial_window_fewer_than_3_games()
    test_win_streak_and_loss_streak()
    test_streak_broken_by_tie()
    test_days_since_last_game()
    test_blowout_flag_threshold()
    test_all_current_season_no_carryover()
    test_prior_season_carryover_2026_opener()
    test_mixed_season_carryover()
    test_standalone_game_zero_competitors()
    test_competing_games_within_window_counted()
    test_window_boundary_exact_edge()
    test_day_of_week_derivation()
    print("\nAll compute_v1 tests passed.")
