"""
features/context_state_as_of.py

Same point-in-time principle as market_state_as_of, applied to
game_context (rankings, TV, records). No book-equivalent concept needed
here -- one source (ESPN), so no normalized child table required; this
feeds directly into engineered_features_v2's context columns.
"""

from features.market_state_as_of import _assert_cutoff_not_after_kickoff, _parse_iso  # noqa: E402


def context_state_as_of(conn, canonical_game_id, point_in_time_cutoff: str):
    """
    Returns a dict with ranking_home, ranking_away, tv_network,
    record_home, record_away, context_observation_timestamp,
    context_staleness_seconds -- or None if no legitimate context
    observation exists at or before the cutoff.
    """
    _assert_cutoff_not_after_kickoff(conn, canonical_game_id, point_in_time_cutoff)

    rows = conn.execute(
        "SELECT observed_at, ranking_home, ranking_away, tv_network, record_home, record_away "
        "FROM game_context WHERE canonical_game_id = ? ORDER BY observed_at ASC",
        (canonical_game_id,),
    ).fetchall()

    cutoff_dt = _parse_iso(point_in_time_cutoff)
    best = None
    best_dt = None
    for row in rows:
        observed_at = row[0]
        observed_dt = _parse_iso(observed_at)
        if observed_dt <= cutoff_dt:
            if best_dt is None or observed_dt > best_dt:
                best = row
                best_dt = observed_dt

    if best is None:
        return None

    observed_at, ranking_home, ranking_away, tv_network, record_home, record_away = best
    staleness_seconds = (cutoff_dt - best_dt).total_seconds()

    return {
        "ranking_home": ranking_home, "ranking_away": ranking_away,
        "tv_network": tv_network, "record_home": record_home, "record_away": record_away,
        "context_observation_timestamp": observed_at,
        "context_staleness_seconds": staleness_seconds,
    }
