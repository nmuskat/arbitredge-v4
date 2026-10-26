"""
validation/feature_sets/rung0_schedule.py

Rung 0: schedule-only Handle Intensity baseline. Reads the already-computed
schedule columns from engineered_features (feature_set_version='v1') --
no new computation, no rivalry/playoff features (backlog, per instruction).
"""

from validation.feature_sets.base import FeatureSet

FEATURE_SET_VERSION = "v1"

COLUMNS = [
    "competing_games_same_window_same_sport",
    "competing_games_same_window_all_sports",
    "is_standalone_same_sport",
    "is_standalone_all_sports",
    "day_of_week",
]


class Rung0ScheduleFeatureSet(FeatureSet):
    rung_name = "rung0_schedule"

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
