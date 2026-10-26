"""
validation/feature_sets/behavioral_handle_intensity_v1.py

Wires HandleIntensityV1 into the validation harness's FeatureSet
interface, WITHOUT modifying the behavioral model in any way. Reuses
behavioral/handle_intensity_v1.py's frozen compute_handle_intensity_v1()
exactly as-is -- no fitting, no tuning, no threshold, no enrichment
activation happens here.

SOURCE: reads from V2's engineered_features_v2, not V1's engineered_features.
V2 is the actual point-in-time-correct layer (arbitrary named cutoffs,
per-game resolved timestamps) that this project already built for exactly
this purpose -- reading from V1 instead would silently lock behavioral
evaluation to kickoff-only and would be a step backward from the
point-in-time integrity this project has maintained throughout.

SLATE RESOLUTION (the real architectural problem this file solves):
HandleIntensityV1 is inherently slate-relative -- it needs a batch of
comparable games, but the harness's FeatureSet.get_features() interface
is called per-game. This wrapper resolves a comparison slate for the
requested (game, cutoff) as follows, DISCLOSED explicitly because it is a
real design choice, not a validated parameter:
  1. Determine this game's relative cutoff type by comparing the
     requested as_of_cutoff against the game's own commence_time (e.g.
     "24 hours before kickoff").
  2. Find all OTHER games of the SAME sport, within a +/- SLATE_WINDOW_DAYS
     window of this game's own commence_time, that have a real
     engineered_features_v2 row at the SAME relative cutoff type
     (matched within a small tolerance for timestamp rounding).
  3. Compute HandleIntensityV1 once over that whole resolved slate.
  4. Return only the requested game's row.

SLATE_WINDOW_DAYS=3 is a disclosed, reasoned-but-UNVALIDATED design
choice (roughly a typical game week) -- explicitly not something real
evidence has yet justified, exactly like every other undetermined
parameter in Behavioral V1 itself.

EVIDENCE LANGUAGE: this FeatureSet exists to let the harness test whether
HandleIntensityV1 shows a relationship CONSISTENT WITH real market-response
evidence (movement magnitude, cross-book disagreement) -- never described
as a direct measurement of recreational betting behavior.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from validation.feature_sets.base import FeatureSet  # noqa: E402
from behavioral.handle_intensity_v1 import compute_handle_intensity_v1  # noqa: E402

FEATURE_SET_VERSION = "v2"
SLATE_WINDOW_DAYS = 3
CUTOFF_MATCH_TOLERANCE_SECONDS = 300


def _parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _relative_offset_seconds(commence_time, point_in_time_cutoff):
    return (_parse_iso(commence_time) - _parse_iso(point_in_time_cutoff)).total_seconds()


def _get_game_row(conn, canonical_game_id):
    return conn.execute(
        "SELECT sport, commence_time FROM games_canonical WHERE canonical_game_id = ?",
        (canonical_game_id,),
    ).fetchone()


def _get_v2_row(conn, canonical_game_id, as_of_cutoff):
    columns = [
        "is_standalone_same_sport", "is_standalone_all_sports",
        "competing_games_same_window_same_sport", "competing_games_same_window_all_sports",
        "ranking_home", "ranking_away", "tv_network",
    ]
    row = conn.execute(
        f"SELECT {', '.join(columns)} FROM engineered_features_v2 "
        f"WHERE canonical_game_id = ? AND feature_set_version = ? AND point_in_time_cutoff = ?",
        (canonical_game_id, FEATURE_SET_VERSION, as_of_cutoff),
    ).fetchone()
    if row is None:
        return None
    return dict(zip(columns, row))


def find_slate_games(conn, canonical_game_id, sport, commence_time, target_offset_seconds,
                      feature_set_version=FEATURE_SET_VERSION, window_days=SLATE_WINDOW_DAYS,
                      tolerance_seconds=CUTOFF_MATCH_TOLERANCE_SECONDS):
    """Shared slate-resolution logic, reused by both the Handle Intensity
    and Side Preference FeatureSet wrappers rather than duplicated."""
    window_start = (_parse_iso(commence_time) - timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    window_end = (_parse_iso(commence_time) + timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    candidates = conn.execute(
        "SELECT ef.canonical_game_id, ef.point_in_time_cutoff, gc.commence_time "
        "FROM engineered_features_v2 ef JOIN games_canonical gc ON ef.canonical_game_id = gc.canonical_game_id "
        "WHERE gc.sport = ? AND ef.feature_set_version = ? "
        "AND gc.commence_time >= ? AND gc.commence_time <= ? "
        "AND ef.canonical_game_id != ?",
        (sport, feature_set_version, window_start, window_end, canonical_game_id),
    ).fetchall()

    matched = []
    for cand_gid, cand_cutoff, cand_commence_time in candidates:
        cand_offset = _relative_offset_seconds(cand_commence_time, cand_cutoff)
        if abs(cand_offset - target_offset_seconds) <= tolerance_seconds:
            matched.append((cand_gid, cand_cutoff))
    return matched


def _find_slate_games(conn, canonical_game_id, sport, commence_time, target_offset_seconds):
    return find_slate_games(conn, canonical_game_id, sport, commence_time, target_offset_seconds)


class BehavioralHandleIntensityV1FeatureSet(FeatureSet):
    rung_name = "behavioral_handle_intensity_v1"

    def get_features(self, conn, canonical_game_id, as_of_cutoff):
        game_row = _get_game_row(conn, canonical_game_id)
        if game_row is None:
            return None
        sport, commence_time = game_row

        v2_row = _get_v2_row(conn, canonical_game_id, as_of_cutoff)
        if v2_row is None:
            return None

        target_offset = _relative_offset_seconds(commence_time, as_of_cutoff)
        slate_matches = _find_slate_games(conn, canonical_game_id, sport, commence_time, target_offset)

        games_for_slate = [{"canonical_game_id": canonical_game_id, **v2_row, "_context": v2_row}]
        for cand_gid, cand_cutoff in slate_matches:
            cand_v2_row = _get_v2_row(conn, cand_gid, cand_cutoff)
            if cand_v2_row is not None:
                games_for_slate.append({"canonical_game_id": cand_gid, **cand_v2_row, "_context": cand_v2_row})

        results = compute_handle_intensity_v1(games_for_slate)
        target_result = next(r for r in results if r["canonical_game_id"] == canonical_game_id)

        return {
            "handle_intensity_normalized_score": target_result["normalized_score"],
            "handle_intensity_percentile_rank": target_result["percentile_rank"],
            "handle_intensity_coverage_count": target_result["coverage_count"],
            "handle_intensity_slate_size": len(games_for_slate),
            "handle_intensity_label": target_result["label"],
        }

    def point_in_time_cutoff_used(self, conn, canonical_game_id):
        row = conn.execute(
            "SELECT point_in_time_cutoff FROM engineered_features_v2 "
            "WHERE canonical_game_id = ? AND feature_set_version = ? ORDER BY computed_at DESC LIMIT 1",
            (canonical_game_id, FEATURE_SET_VERSION),
        ).fetchone()
        return row[0] if row else None
