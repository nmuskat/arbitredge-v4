"""
features/compute_v1.py

Pure computation logic for Feature Set V1. No database writes here -- this
module takes already-fetched, in-memory data and returns a dict of
computed feature values. Kept separate from build_engineered_features_v1.py
(the orchestration script) specifically so this logic is directly unit
testable without needing a live database connection for every test.

STRICT POINT-IN-TIME RULE, enforced structurally, not just by convention:
every function that considers a team's prior games requires the caller to
have already filtered that history to games with commence_time strictly
before the target game's commence_time (see get_team_history in
build_engineered_features_v1.py, which performs this filtering at the SQL
level before any row reaches this module). This module additionally
defends against a leakage bug at the point of use: compute_team_features
raises ValueError if any history entry's commence_time is not strictly
before the provided target_commence_time, so a caller mistake fails loudly
rather than silently producing a leaked feature.

NO betting/behavioral scoring exists anywhere in this module: no public
pressure, no simulated ticket/handle percentage, no sharp/square score, no
Book Need score, no manually chosen weight. Every value returned is a
direct, disclosed count or average of raw historical facts.

BLOWOUT THRESHOLD (>=21 point margin) is a V1 FEATURE-DEFINITION HEURISTIC,
not a validated bettor-behavior threshold -- see BLOWOUT_MARGIN_THRESHOLD
below. It exists to give a transparent, revisitable boolean flag alongside
the continuous point-margin fields, which are the actual underlying
measurement.
"""

from datetime import datetime
from statistics import mean
from typing import Optional

BLOWOUT_MARGIN_THRESHOLD = 21  # V1 heuristic, not a validated psychological threshold -- see module docstring
COMPETING_WINDOW_MINUTES = 60  # symmetric +/- window for "same window" kickoff-time proximity


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def compute_team_features(history: list, target_commence_time: str, target_season_year: Optional[int]) -> dict:
    """
    history: list of dicts, ALREADY filtered to strictly-prior games for one
        team, ordered most-recent-first. Each dict must have keys:
        commence_time, result ('W'/'L'/'T'), points_for, points_against,
        margin, season_year.
    target_commence_time: the target game's own commence_time (ISO8601 string).
    target_season_year: the target game's own season_year (from its own
        game_results row), used only for season-carryover transparency
        fields -- never used to filter which prior games are eligible.

    Returns a dict of every field name (WITHOUT home_/away_ prefix -- the
    caller prefixes when assembling the final row) for ONE team. All NULL
    (Python None) when games_played_before_this == 0.
    """
    target_dt = _parse_iso(target_commence_time)
    for g in history:
        if _parse_iso(g["commence_time"]) >= target_dt:
            raise ValueError(
                f"Look-ahead leakage detected: history entry with commence_time {g['commence_time']!r} "
                f"is not strictly before target_commence_time {target_commence_time!r}. "
                f"The caller must filter history before calling compute_team_features."
            )

    games_played_before_this = len(history)
    last3 = history[:3]
    last5 = history[:5]
    last3_sample_size = len(last3)
    last5_sample_size = len(last5)

    result = {
        "games_played_before_this": games_played_before_this,
        "last3_sample_size": last3_sample_size,
        "last5_sample_size": last5_sample_size,
    }

    if games_played_before_this == 0:
        result.update({
            "current_season_games_before_this": 0,
            "last_game_commence_time": None,
            "wins_last_3": None, "wins_last_5": None,
            "points_for_avg_last_3": None, "points_against_avg_last_3": None,
            "point_margin_avg_last_3": None, "point_margin_avg_last_5": None,
            "current_win_streak": None, "current_loss_streak": None,
            "days_since_last_game": None,
            "last_game_result": None, "last_game_point_margin": None,
            "last_game_points_for": None, "last_game_points_against": None,
            "last_game_blowout_win": None, "last_game_blowout_loss": None,
            "last3_prior_season_games": None, "last5_prior_season_games": None,
            "last_game_same_season": None,
        })
        return result

    last = history[0]

    win_streak = 0
    for g in history:
        if g["result"] == "W":
            win_streak += 1
        else:
            break
    loss_streak = 0
    for g in history:
        if g["result"] == "L":
            loss_streak += 1
        else:
            break

    days_since_last_game = (target_dt - _parse_iso(last["commence_time"])).days

    last_game_point_margin = last["margin"]

    current_season_games_before_this = sum(
        1 for g in history if target_season_year is not None and g["season_year"] == target_season_year
    )
    last3_prior_season_games = sum(
        1 for g in last3 if target_season_year is None or g["season_year"] != target_season_year
    )
    last5_prior_season_games = sum(
        1 for g in last5 if target_season_year is None or g["season_year"] != target_season_year
    )
    last_game_same_season = (
        1 if (target_season_year is not None and last["season_year"] == target_season_year) else 0
    )

    result.update({
        "current_season_games_before_this": current_season_games_before_this,
        "last_game_commence_time": last["commence_time"],
        "wins_last_3": sum(1 for g in last3 if g["result"] == "W"),
        "wins_last_5": sum(1 for g in last5 if g["result"] == "W"),
        "points_for_avg_last_3": mean(g["points_for"] for g in last3),
        "points_against_avg_last_3": mean(g["points_against"] for g in last3),
        "point_margin_avg_last_3": mean(g["margin"] for g in last3),
        "point_margin_avg_last_5": mean(g["margin"] for g in last5),
        "current_win_streak": win_streak,
        "current_loss_streak": loss_streak,
        "days_since_last_game": days_since_last_game,
        "last_game_result": last["result"],
        "last_game_point_margin": last_game_point_margin,
        "last_game_points_for": last["points_for"],
        "last_game_points_against": last["points_against"],
        "last_game_blowout_win": 1 if last_game_point_margin >= BLOWOUT_MARGIN_THRESHOLD else 0,
        "last_game_blowout_loss": 1 if last_game_point_margin <= -BLOWOUT_MARGIN_THRESHOLD else 0,
        "last3_prior_season_games": last3_prior_season_games,
        "last5_prior_season_games": last5_prior_season_games,
        "last_game_same_season": last_game_same_season,
    })
    return result


def compute_slate_features(all_games: list, target_canonical_game_id, target_sport: str, target_commence_time: str) -> dict:
    """
    all_games: list of dicts with keys canonical_game_id, sport, commence_time
        for EVERY game in games_canonical (not filtered to the cohort --
        "competing games" should count real scheduled games broadly).

    Schedule-safe by design: this does NOT require a strictly-prior-game
    filter, since a season's schedule is knowable in advance -- only
    RESULT-based features need the strict point-in-time cutoff. See the
    build script's docstring for the historical-hindsight caveat: using
    the full completed season schedule is fine for the 2025 backfill; a
    live RUN SLATE must use the schedule state actually known at signal
    time, accounting for postponements -- documented, not solved here.
    """
    target_dt = _parse_iso(target_commence_time)
    window_start = target_dt.timestamp() - COMPETING_WINDOW_MINUTES * 60
    window_end = target_dt.timestamp() + COMPETING_WINDOW_MINUTES * 60

    same_sport_count = 0
    all_sports_count = 0
    for g in all_games:
        if g["canonical_game_id"] == target_canonical_game_id:
            continue
        g_dt = _parse_iso(g["commence_time"])
        if window_start <= g_dt.timestamp() <= window_end:
            all_sports_count += 1
            if g["sport"] == target_sport:
                same_sport_count += 1

    day_of_week = target_dt.strftime("%A")

    return {
        "competing_games_same_window_same_sport": same_sport_count,
        "competing_games_same_window_all_sports": all_sports_count,
        "is_standalone_same_sport": 1 if same_sport_count == 0 else 0,
        "is_standalone_all_sports": 1 if all_sports_count == 0 else 0,
        "day_of_week": day_of_week,
    }
