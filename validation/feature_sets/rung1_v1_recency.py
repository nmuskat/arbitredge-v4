"""
validation/feature_sets/rung1_v1_recency.py

Rung 1: Rung 0's schedule columns PLUS V1's recency/rest columns
(cumulative, not recency-only) -- so an ablation test of Rung 0 vs Rung 1
isolates the marginal contribution of recency/rest information specifically,
per standard nested-model ablation. Reads only already-validated
engineered_features columns; the gated 2025-NULL fields (rankings,
tv_network, favorite_side, spread_magnitude) are deliberately excluded here
too, since they're 100% NULL for the whole 2025 cohort and would only add
noise to this rung's feature vector.
"""

from validation.feature_sets.base import FeatureSet
from validation.feature_sets.rung0_schedule import COLUMNS as RUNG0_COLUMNS

FEATURE_SET_VERSION = "v1"

RECENCY_REST_COLUMNS = [
    "home_wins_last_3", "away_wins_last_3", "home_wins_last_5", "away_wins_last_5",
    "home_points_for_avg_last_3", "away_points_for_avg_last_3",
    "home_points_against_avg_last_3", "away_points_against_avg_last_3",
    "home_point_margin_avg_last_3", "away_point_margin_avg_last_3",
    "home_point_margin_avg_last_5", "away_point_margin_avg_last_5",
    "home_current_win_streak", "away_current_win_streak",
    "home_current_loss_streak", "away_current_loss_streak",
    "home_days_since_last_game", "away_days_since_last_game",
    "rest_advantage_days",
    "home_last_game_point_margin", "away_last_game_point_margin",
    "home_last_game_blowout_win", "away_last_game_blowout_win",
    "home_last_game_blowout_loss", "away_last_game_blowout_loss",
]

COLUMNS = RUNG0_COLUMNS + RECENCY_REST_COLUMNS


class Rung1V1RecencyFeatureSet(FeatureSet):
    rung_name = "rung1_v1_recency"

    def get_features(self, conn, canonical_game_id, as_of_cutoff):
        row = conn.execute(
            f"SELECT {', '.join(COLUMNS)}, point_in_time_cutoff FROM engineered_features "
            f"WHERE canonical_game_id = ? AND feature_set_version = ?",
            (canonical_game_id, FEATURE_SET_VERSION),
        ).fetchone()
        if row is None:
            return None
        values = row[:-1]
        return dict(zip(COLUMNS, values))

    def point_in_time_cutoff_used(self, conn, canonical_game_id):
        row = conn.execute(
            "SELECT point_in_time_cutoff FROM engineered_features "
            "WHERE canonical_game_id = ? AND feature_set_version = ?",
            (canonical_game_id, FEATURE_SET_VERSION),
        ).fetchone()
        return row[0] if row else None
