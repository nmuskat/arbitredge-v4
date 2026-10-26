"""
features/build_engineered_features_v2.py

V2 orchestration, separated from pure computation exactly like V1's
build_engineered_features_v1.py. Reuses features/compute_v1.py's already-
tested pure functions (compute_team_features, compute_slate_features,
get_prior_games via build_team_index) WITHOUT modifying them.

CRITICAL USAGE DISCIPLINE (the one real finding from the architecture
audit before this was built): compute_team_features and get_prior_games
are called with `point_in_time_cutoff` as their time boundary -- correct,
that's exactly what point-in-time recency means. compute_slate_features
is called with the game's REAL commence_time, NEVER the cutoff --
day_of_week and competing_games_same_window_* are properties of the game
itself (when does it actually kick off, what else is airing around ITS
real kickoff), not of the evaluation moment. Passing cutoff into
compute_slate_features would silently compute the wrong day of week and
the wrong competing-games count. This distinction is enforced here, not
inside compute_v1.py, which is untouched.

SEASON-TYPE SAFEGUARD: persist_features_v2 defaults to
require_regular_season=True, refusing to persist unless
game_results.season_type == 2 is confirmed for that game (same check V1
uses). Preseason/mechanics testing must explicitly pass
require_regular_season=False -- a deliberate, visible choice, never an
accidental default.
"""

from datetime import datetime

from features.compute_v1 import compute_team_features, compute_slate_features
from features.build_engineered_features_v1 import (
    get_all_games, get_latest_results_by_canonical_game, build_team_index, get_prior_games,
)
from features.context_state_as_of import context_state_as_of
from features.market_state_as_of import _assert_cutoff_not_after_kickoff


def _get_game_row(conn, canonical_game_id):
    row = conn.execute(
        "SELECT sport, home_team_id, away_team_id, commence_time FROM games_canonical "
        "WHERE canonical_game_id = ?", (canonical_game_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"canonical_game_id {canonical_game_id} not found")
    return row


def _get_season_type_year(conn, canonical_game_id):
    row = conn.execute(
        "SELECT season_year FROM game_results WHERE canonical_game_id = ? "
        "ORDER BY retrieved_at DESC LIMIT 1",
        (canonical_game_id,),
    ).fetchone()
    return row[0] if row else None


def compute_features_v2(conn, canonical_game_id, point_in_time_cutoff, team_index=None, all_games=None, latest_results=None):
    """
    Pure-ish computation (reads the DB, writes nothing). Returns a dict of
    all engineered_features_v2 columns except feature_id/feature_set_version/
    computed_at, which the persistence wrapper fills in.
    """
    _assert_cutoff_not_after_kickoff(conn, canonical_game_id, point_in_time_cutoff)

    sport, home_id, away_id, commence_time = _get_game_row(conn, canonical_game_id)

    if all_games is None:
        all_games = get_all_games(conn)
    if latest_results is None:
        latest_results = get_latest_results_by_canonical_game(conn)
    if team_index is None:
        team_index = build_team_index(all_games, latest_results)

    target_season_year = _get_season_type_year(conn, canonical_game_id)

    # Recency features: bounded by the CUTOFF -- this is the actual point-in-time behavior
    home_history = get_prior_games(team_index, home_id, point_in_time_cutoff)
    away_history = get_prior_games(team_index, away_id, point_in_time_cutoff)
    home_features = compute_team_features(home_history, point_in_time_cutoff, target_season_year)
    away_features = compute_team_features(away_history, point_in_time_cutoff, target_season_year)

    # Slate features: bounded by the game's REAL commence_time, NEVER the cutoff -- see module docstring
    slate_features = compute_slate_features(all_games, canonical_game_id, sport, commence_time)

    # Context: as-of the SAME cutoff as the recency features
    context = context_state_as_of(conn, canonical_game_id, point_in_time_cutoff)

    row = {"canonical_game_id": canonical_game_id, "point_in_time_cutoff": point_in_time_cutoff}
    for k, v in home_features.items():
        row[f"home_{k}"] = v
    for k, v in away_features.items():
        row[f"away_{k}"] = v
    row.update(slate_features)

    if context is not None:
        row["ranking_home"] = context["ranking_home"]
        row["ranking_away"] = context["ranking_away"]
        row["tv_network"] = context["tv_network"]
        row["record_home"] = context["record_home"]
        row["record_away"] = context["record_away"]
        row["context_observation_timestamp"] = context["context_observation_timestamp"]
        row["context_staleness_seconds"] = context["context_staleness_seconds"]
    else:
        for k in ["ranking_home", "ranking_away", "tv_network", "record_home", "record_away",
                  "context_observation_timestamp", "context_staleness_seconds"]:
            row[k] = None

    if row.get("home_games_played_before_this", 0) and row.get("away_games_played_before_this", 0):
        hd = row.get("home_days_since_last_game")
        ad = row.get("away_days_since_last_game")
        row["rest_advantage_days"] = (hd - ad) if (hd is not None and ad is not None) else None
    else:
        row["rest_advantage_days"] = None

    return row


COLUMN_ORDER = [
    "canonical_game_id", "feature_set_version", "computed_at", "point_in_time_cutoff",
    "home_games_played_before_this", "away_games_played_before_this",
    "home_last3_sample_size", "away_last3_sample_size",
    "home_last5_sample_size", "away_last5_sample_size",
    "home_current_season_games_before_this", "away_current_season_games_before_this",
    "home_last_game_commence_time", "away_last_game_commence_time",
    "home_wins_last_3", "away_wins_last_3", "home_wins_last_5", "away_wins_last_5",
    "home_points_for_avg_last_3", "away_points_for_avg_last_3",
    "home_points_against_avg_last_3", "away_points_against_avg_last_3",
    "home_point_margin_avg_last_3", "away_point_margin_avg_last_3",
    "home_point_margin_avg_last_5", "away_point_margin_avg_last_5",
    "home_current_win_streak", "away_current_win_streak",
    "home_current_loss_streak", "away_current_loss_streak",
    "home_days_since_last_game", "away_days_since_last_game",
    "rest_advantage_days",
    "home_last_game_result", "away_last_game_result",
    "home_last_game_point_margin", "away_last_game_point_margin",
    "home_last_game_points_for", "away_last_game_points_for",
    "home_last_game_points_against", "away_last_game_points_against",
    "home_last_game_blowout_win", "away_last_game_blowout_win",
    "home_last_game_blowout_loss", "away_last_game_blowout_loss",
    "home_last3_prior_season_games", "away_last3_prior_season_games",
    "home_last5_prior_season_games", "away_last5_prior_season_games",
    "home_last_game_same_season", "away_last_game_same_season",
    "competing_games_same_window_same_sport", "competing_games_same_window_all_sports",
    "is_standalone_same_sport", "is_standalone_all_sports", "day_of_week",
    "ranking_home", "ranking_away", "tv_network", "record_home", "record_away",
    "context_observation_timestamp", "context_staleness_seconds",
]


def persist_features_v2(conn, canonical_game_id, point_in_time_cutoff, feature_set_version="v2",
                         require_regular_season=True, team_index=None, all_games=None, latest_results=None):
    """
    Orchestration wrapper. Option-C idempotency (unchanged row = no-op).
    require_regular_season=True (default) refuses to persist unless
    game_results.season_type == 2 is confirmed -- preseason/mechanics
    testing must explicitly pass False, a deliberate, visible choice.
    """
    if require_regular_season:
        season_type = conn.execute(
            "SELECT season_type FROM game_results WHERE canonical_game_id = ? "
            "ORDER BY retrieved_at DESC LIMIT 1",
            (canonical_game_id,),
        ).fetchone()
        if season_type is None or season_type[0] != 2:
            return "refused_not_regular_season"

    existing = conn.execute(
        "SELECT feature_id FROM engineered_features_v2 "
        "WHERE canonical_game_id = ? AND feature_set_version = ? AND point_in_time_cutoff = ?",
        (canonical_game_id, feature_set_version, point_in_time_cutoff),
    ).fetchone()
    if existing is not None:
        return "unchanged"

    row = compute_features_v2(conn, canonical_game_id, point_in_time_cutoff, team_index, all_games, latest_results)
    row["feature_set_version"] = feature_set_version
    row["computed_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    for col in COLUMN_ORDER:
        row.setdefault(col, None)

    placeholders = ", ".join(["?"] * len(COLUMN_ORDER))
    conn.execute(
        f"INSERT INTO engineered_features_v2 ({', '.join(COLUMN_ORDER)}) VALUES ({placeholders})",
        [row[c] for c in COLUMN_ORDER],
    )
    conn.commit()
    return "new"
