"""
validation/feature_sets/behavioral_side_preference_v1.py

Wires RecreationalSidePreferenceV1 into the validation harness's
FeatureSet interface, WITHOUT modifying the behavioral model. Reuses
behavioral/side_preference_v1.py's frozen compute_side_preference_v1()
exactly as-is.

Reuses the SAME slate-resolution primitive as the Handle Intensity
wrapper (find_slate_games, in behavioral_handle_intensity_v1.py) rather
than duplicating that logic -- same disclosed SLATE_WINDOW_DAYS=3 design
choice, same V2 source, same reasoning.

EVIDENCE LANGUAGE: this FeatureSet exists to let the harness test whether
RecreationalSidePreferenceV1's signed lean shows a relationship
CONSISTENT WITH real directional market-response evidence (movement
direction specifically) -- never described as a direct measurement of
recreational betting behavior.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from validation.feature_sets.base import FeatureSet  # noqa: E402
from behavioral.side_preference_v1 import compute_side_preference_v1  # noqa: E402
from validation.feature_sets.behavioral_handle_intensity_v1 import (  # noqa: E402
    FEATURE_SET_VERSION, _relative_offset_seconds, _get_game_row, find_slate_games,
)


def _get_v2_side_preference_row(conn, canonical_game_id, as_of_cutoff):
    columns = [
        "home_wins_last_3", "away_wins_last_3",
        "home_point_margin_avg_last_3", "away_point_margin_avg_last_3",
        "home_current_win_streak", "home_current_loss_streak",
        "away_current_win_streak", "away_current_loss_streak",
        "home_last_game_blowout_win", "home_last_game_blowout_loss",
        "away_last_game_blowout_win", "away_last_game_blowout_loss",
        "rest_advantage_days", "ranking_home", "ranking_away",
    ]
    row = conn.execute(
        f"SELECT {', '.join(columns)} FROM engineered_features_v2 "
        f"WHERE canonical_game_id = ? AND feature_set_version = ? AND point_in_time_cutoff = ?",
        (canonical_game_id, FEATURE_SET_VERSION, as_of_cutoff),
    ).fetchone()
    if row is None:
        return None
    return dict(zip(columns, row))


class BehavioralSidePreferenceV1FeatureSet(FeatureSet):
    rung_name = "behavioral_side_preference_v1"

    def get_features(self, conn, canonical_game_id, as_of_cutoff):
        game_row = _get_game_row(conn, canonical_game_id)
        if game_row is None:
            return None
        sport, commence_time = game_row

        v2_row = _get_v2_side_preference_row(conn, canonical_game_id, as_of_cutoff)
        if v2_row is None:
            return None

        target_offset = _relative_offset_seconds(commence_time, as_of_cutoff)
        slate_matches = find_slate_games(conn, canonical_game_id, sport, commence_time, target_offset)

        games_for_slate = [{"canonical_game_id": canonical_game_id, **v2_row, "_context": v2_row}]
        for cand_gid, cand_cutoff in slate_matches:
            cand_v2_row = _get_v2_side_preference_row(conn, cand_gid, cand_cutoff)
            if cand_v2_row is not None:
                games_for_slate.append({"canonical_game_id": cand_gid, **cand_v2_row, "_context": cand_v2_row})

        results = compute_side_preference_v1(games_for_slate)
        target_result = next(r for r in results if r["canonical_game_id"] == canonical_game_id)

        return {
            "side_preference_normalized_lean_score": target_result["normalized_lean_score"],
            "side_preference_direction": target_result["direction"],
            "side_preference_direction_convention": target_result["direction_convention"],
            "side_preference_coverage_count": target_result["coverage_count"],
            "side_preference_slate_size": len(games_for_slate),
            "side_preference_label": target_result["label"],
        }

    def point_in_time_cutoff_used(self, conn, canonical_game_id):
        row = conn.execute(
            "SELECT point_in_time_cutoff FROM engineered_features_v2 "
            "WHERE canonical_game_id = ? AND feature_set_version = ? ORDER BY computed_at DESC LIMIT 1",
            (canonical_game_id, FEATURE_SET_VERSION),
        ).fetchone()
        return row[0] if row else None
