import os
"""
book_need/public_pressure.py

Public Pressure, computed FRESH at a segment's start cutoff -- NOT read
from the persisted engineered_features_v2 research table. That table
only ever contains CONFIRMED regular-season games (via
persist_features_v2's require_regular_season gate), but Week 1 RUN SLATE
targets upcoming games with no confirmed result -- exactly the games
that table is structurally guaranteed to be empty for. This mirrors
run_slate/orchestrate.py's live compute_features_v2 pattern, extended to
build a live comparison slate for the frozen behavioral models.

Behavioral V1 itself is reused completely unmodified.
"""

from datetime import timedelta
from statistics import median

from features.build_engineered_features_v2 import compute_features_v2
from features.market_state_as_of import resolve_named_cutoff, _parse_iso, PostKickoffCutoffError
from behavioral.handle_intensity_v1 import compute_handle_intensity_v1
from behavioral.handle_intensity_v2 import compute_handle_intensity_v2
from behavioral.handle_intensity_cfb_v2 import compute_handle_intensity_v2 as compute_handle_intensity_cfb_v2
from behavioral.side_preference_v1 import compute_side_preference_v1, _derive_raw_features, PRIMARY_FEATURES, FEATURE_SIGNS
from behavioral.side_preference_nfl_v2 import compute_side_preference_nfl_v2
from behavioral._stats import slate_percentile_ranks, reference_zscore, reference_percentile_rank

SLATE_WINDOW_DAYS = 3

def _find_historical_nfl_reference_games(conn, target_commence_time, target_cutoff):
    """
    Return prior NFL regular-season games with final results known as-of
    the target cutoff.

    Future games, non-NFL games, non-final results, non-regular-season
    results, and results retrieved only after the target cutoff are excluded.
    """
    return conn.execute(
        "SELECT gc.canonical_game_id, gc.commence_time "
        "FROM games_canonical gc "
        "WHERE gc.sport='nfl' "
        "AND gc.commence_time < ? "
        "AND EXISTS ("
        "  SELECT 1 FROM game_results gr "
        "  WHERE gr.canonical_game_id = gc.canonical_game_id "
        "  AND gr.season_type = 2 "
        "  AND gr.is_final = 1 "
        "  AND gr.retrieved_at <= ?"
        ") "
        "ORDER BY gc.commence_time ASC",
        (target_commence_time, target_cutoff),
    ).fetchall()



def _compute_standalone_nfl_side_preference(
    conn,
    canonical_game_id,
    commence_time,
    named_cutoff,
    target_cutoff,
    target_features,
):
    """
    Score a standalone NFL target against prior regular-season NFL games
    whose final results were known by the target cutoff.

    Historical games are reconstructed at their own equivalent named cutoff.
    The target is never inserted into the reference distribution.
    """
    reference_rows = _find_historical_nfl_reference_games(
        conn,
        target_commence_time=commence_time,
        target_cutoff=target_cutoff,
    )

    reference_games = []
    for ref_gid, ref_commence_time in reference_rows:
        try:
            ref_cutoff = resolve_named_cutoff(ref_commence_time, named_cutoff)
            ref_features = compute_features_v2(conn, ref_gid, ref_cutoff)
        except PostKickoffCutoffError:
            continue

        reference_game = {
            "canonical_game_id": ref_gid,
            **ref_features,
            "_context": ref_features,
        }

        reference_raw = _derive_raw_features(ref_features, ref_features)
        if not all(reference_raw.get(feature) is not None for feature in PRIMARY_FEATURES):
            continue

        reference_games.append(reference_game)

    if len(reference_games) < 2:
        return None

    target_raw = _derive_raw_features(target_features, target_features)
    reference_raw = [
        _derive_raw_features(game, game.get("_context"))
        for game in reference_games
    ]

    contributions = {}
    coverage_count = 0

    for feature in PRIMARY_FEATURES:
        value = target_raw.get(feature)
        if value is None:
            contributions[feature] = 0.0
            continue

        reference_values = [row.get(feature) for row in reference_raw]
        contributions[feature] = (
            FEATURE_SIGNS[feature]
            * reference_zscore(value, reference_values)
        )
        coverage_count += 1

    raw_lean_score = sum(contributions.values())
    normalized_lean_score = (
        raw_lean_score / coverage_count if coverage_count > 0 else 0.0
    )

    if normalized_lean_score > 0:
        direction = "home"
    elif normalized_lean_score < 0:
        direction = "away"
    else:
        direction = "even"

    reference_results = compute_side_preference_v1(reference_games)
    reference_magnitudes = [
        abs(row["normalized_lean_score"])
        for row in reference_results
    ]

    distinct_nonzero = set(
        magnitude for magnitude in reference_magnitudes if magnitude != 0
    )
    magnitude_undefined = len(distinct_nonzero) < 2

    magnitude_percentile = (
        None
        if magnitude_undefined
        else reference_percentile_rank(
            abs(normalized_lean_score),
            reference_magnitudes,
        )
    )

    return {
        "direction": direction,
        "normalized_lean_score": normalized_lean_score,
        "magnitude_percentile": magnitude_percentile,
        "magnitude_undefined": magnitude_undefined,
        "coverage_count": coverage_count,
        "reference_game_count": len(reference_games),
    }



def _find_live_slate_games(conn, canonical_game_id, sport, commence_time):
    window_start = (_parse_iso(commence_time) - timedelta(days=SLATE_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    window_end = (_parse_iso(commence_time) + timedelta(days=SLATE_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return conn.execute(
        "SELECT canonical_game_id, commence_time FROM games_canonical "
        "WHERE sport=? AND commence_time>=? AND commence_time<=? AND canonical_game_id != ?",
        (sport, window_start, window_end, canonical_game_id),
    ).fetchall()



def _find_nfl_week_games(conn, canonical_game_id, commence_time):
    """
    Return the target game's NFL weekly comparison population.

    NFL weeks are grouped Thursday 00:00 UTC through the following
    Thursday 00:00 UTC so Thursday, Sunday, and Monday standalone
    games are compared with the complete surrounding NFL schedule.
    """
    target_dt = _parse_iso(commence_time)
    days_since_thursday = (target_dt.weekday() - 3) % 7
    week_start = (
        target_dt - timedelta(days=days_since_thursday)
    ).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)

    return conn.execute(
        "SELECT canonical_game_id, commence_time FROM games_canonical "
        "WHERE sport='nfl' AND commence_time>=? AND commence_time<? "
        "AND canonical_game_id != ? "
        "ORDER BY commence_time",
        (
            week_start.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            week_end.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            canonical_game_id,
        ),
    ).fetchall()


def compute_public_pressure(conn, canonical_game_id, sport, commence_time, named_cutoff, hi_as_of=None):
    """named_cutoff: 'T-24h'/'T-6h'/'T-1h' -- resolved fresh against the
    TARGET game's own commence_time AND, independently, against every
    slate candidate's own commence_time (never a shared literal
    timestamp), then compute_features_v2 is called fresh -- pure, no
    persistence dependency -- for each. Returns None if the target
    game's own features can't be computed at this cutoff."""
    target_cutoff = resolve_named_cutoff(commence_time, named_cutoff)
    try:
        target_features = compute_features_v2(conn, canonical_game_id, target_cutoff)
    except PostKickoffCutoffError:
        return None

    games_for_slate = [{"canonical_game_id": canonical_game_id, **target_features, "_context": target_features}]

    for cand_gid, cand_commence_time in _find_live_slate_games(conn, canonical_game_id, sport, commence_time):
        try:
            cand_cutoff = resolve_named_cutoff(cand_commence_time, named_cutoff)
            cand_features = compute_features_v2(conn, cand_gid, cand_cutoff)
        except PostKickoffCutoffError:
            continue
        games_for_slate.append({"canonical_game_id": cand_gid, **cand_features, "_context": cand_features})

    hi_games = games_for_slate

    if sport == "nfl":
        hi_games = [{
            "canonical_game_id": canonical_game_id,
            **target_features,
            "_context": target_features,
            "_hi_as_of": target_cutoff,
        }]

        for cand_gid, cand_commence_time in _find_nfl_week_games(
            conn,
            canonical_game_id,
            commence_time,
        ):
            try:
                cand_cutoff = resolve_named_cutoff(
                    cand_commence_time,
                    named_cutoff,
                )
                cand_features = compute_features_v2(
                    conn,
                    cand_gid,
                    cand_cutoff,
                )
            except PostKickoffCutoffError:
                continue

            hi_games.append({
                "canonical_game_id": cand_gid,
                **cand_features,
                "_context": cand_features,
                "_hi_as_of": cand_cutoff,
            })

    hi_results = compute_handle_intensity_v1(hi_games)
    hi_target = next(
        r for r in hi_results
        if r["canonical_game_id"] == canonical_game_id
    )

    # Experimental NFL Handle Intensity V2.
    # Computed alongside V1 for validation only. V1 remains authoritative.
    # V2 uses the inference evaluation horizon when supplied, preserving
    # strict point-in-time behavior while allowing in-segment market activity.
    hi_v2_target = None
    if sport == "nfl":
        if hi_as_of is not None:
            for game in hi_games:
                game["_hi_as_of"] = hi_as_of

        hi_v2_results = compute_handle_intensity_v2(conn, hi_games)
        hi_v2_target = next(
            r for r in hi_v2_results
            if r["canonical_game_id"] == canonical_game_id
        )

        if os.environ.get("ARBITREDGE_HI_V2_DEBUG") == "1":
            print(
                f"HI_COMPARE gid={canonical_game_id} "
                f"v1={hi_target.get('percentile_rank')} "
                f"v2={hi_v2_target.get('percentile_rank')}"
            )

    # CFB Handle Intensity V2.
    # Computed alongside V1 for prospective validation only.
    # CFB V1 remains authoritative until explicitly promoted.
    cfb_hi_v2_target = None
    if sport == "cfb":
        if hi_as_of is not None:
            for game in hi_games:
                game["_hi_as_of"] = hi_as_of

        cfb_hi_v2_results = compute_handle_intensity_cfb_v2(conn, hi_games)
        cfb_hi_v2_target = next(
            r for r in cfb_hi_v2_results
            if r["canonical_game_id"] == canonical_game_id
        )

        if os.environ.get("ARBITREDGE_HI_V2_DEBUG") == "1":
            print(
                f"CFB_HI_COMPARE gid={canonical_game_id} "
                f"v1={hi_target.get('percentile_rank')} "
                f"v2={cfb_hi_v2_target.get('percentile_rank')}"
            )

    # NFL Side Preference V2:
    # Attach consensus home spread at each game's equivalent named cutoff.
    if sport == "nfl":
        for game in games_for_slate:
            gid = game["canonical_game_id"]

            row = conn.execute(
                "SELECT commence_time FROM games_canonical "
                "WHERE canonical_game_id=?",
                (gid,),
            ).fetchone()

            if row is None:
                game["_consensus_home_spread"] = None
                continue

            game_cutoff = resolve_named_cutoff(row[0], named_cutoff)

            spread_rows = conn.execute(
                "SELECT ms.spread_home "
                "FROM market_snapshots ms "
                "JOIN ("
                " SELECT book, MAX(observed_at) AS latest "
                " FROM market_snapshots "
                " WHERE canonical_game_id=? "
                " AND observed_at<=? "
                " AND spread_home IS NOT NULL "
                " GROUP BY book"
                ") x ON x.latest=ms.observed_at AND x.book=ms.book "
                "WHERE ms.canonical_game_id=? "
                "AND ms.spread_home IS NOT NULL",
                (gid, game_cutoff, gid),
            ).fetchall()

            spreads = [float(r[0]) for r in spread_rows]
            game["_consensus_home_spread"] = (
                median(spreads) if spreads else None
            )

    standalone_nfl = (
        sport == "nfl"
        and target_features.get("is_standalone_same_sport") == 1
    )

    historical_sp = None
    if standalone_nfl:
        historical_sp = _compute_standalone_nfl_side_preference(
            conn,
            canonical_game_id=canonical_game_id,
            commence_time=commence_time,
            named_cutoff=named_cutoff,
            target_cutoff=target_cutoff,
            target_features=target_features,
        )

    if historical_sp is not None:
        sp_direction = historical_sp["direction"]
        sp_magnitude_percentile = historical_sp["magnitude_percentile"]
        magnitude_undefined = historical_sp["magnitude_undefined"]
        sp_coverage_count = historical_sp["coverage_count"]
    else:
        if sport == "nfl":
            sp_results = compute_side_preference_nfl_v2(games_for_slate)
        else:
            sp_results = compute_side_preference_v1(games_for_slate)

        sp_target = next(
            r for r in sp_results
            if r["canonical_game_id"] == canonical_game_id
        )

        sp_magnitudes = [
            abs(r["normalized_lean_score"])
            for r in sp_results
        ]
        distinct_nonzero = set(
            magnitude for magnitude in sp_magnitudes
            if magnitude != 0
        )
        magnitude_undefined = len(distinct_nonzero) < 2

        sp_ids = [r["canonical_game_id"] for r in sp_results]
        sp_target_idx = sp_ids.index(canonical_game_id)
        sp_percentiles = slate_percentile_ranks(sp_magnitudes)

        sp_magnitude_percentile = (
            None
            if magnitude_undefined
            else sp_percentiles[sp_target_idx]
        )
        sp_direction = sp_target["direction"]
        sp_coverage_count = sp_target["coverage_count"]

    active_hi = hi_v2_target if sport == "nfl" and hi_v2_target is not None else hi_target

    return {
        "handle_intensity_percentile": active_hi["percentile_rank"],
        "handle_intensity_coverage_count": active_hi["coverage_count"],
        "side_preference_direction": sp_direction,
        "side_preference_magnitude_percentile": sp_magnitude_percentile,
        "side_preference_magnitude_undefined": magnitude_undefined,
        "side_preference_coverage_count": sp_coverage_count,
    }
