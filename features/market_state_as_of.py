"""
features/market_state_as_of.py

The core V2 point-in-time market primitive. Distinct from
reporting/checkpoint_reconstruction.py's nearest-within-tolerance named
checkpoints (a different tool for a different purpose -- "the best real
observation near a landmark," for descriptive reporting): this is "the
LATEST observation legitimately known at an EXACT cutoff," for leak-free
feature computation at an arbitrary point in time.

RULE: among a book's observations with effective_time <= point_in_time_cutoff,
return the latest one. Never an observation after the cutoff. Missing data
returns None -- never interpolated, never fabricated.

book is a REQUIRED parameter with NO default and no internal selection
logic. Per the locked V2 architecture: no consensus, no averaging, no
book weighting, no fixed reference book -- every caller must explicitly
choose which book's state it wants.

STRUCTURAL LEAKAGE REJECTION: cutoff > commence_time is a hard error, not
advisory. A future debugging tool that deliberately inspects post-kickoff
states would need to be a SEPARATE utility -- this interface refuses.
"""

from datetime import datetime, timedelta


class PostKickoffCutoffError(ValueError):
    pass


def _parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def resolve_named_cutoff(commence_time: str, named_cutoff: str) -> str:
    """Translates a symbolic research cutoff ('T-24h', 'T-6h', 'T-1h') into
    a real, resolved ISO timestamp. Named cutoffs NEVER enter the schema
    or any persisted table as symbolic labels -- only the resolved
    timestamp this function returns does. Also accepts 'kickoff' (returns
    commence_time itself) for convenience."""
    kickoff = _parse_iso(commence_time)
    mapping = {"kickoff": 0, "T-24h": 24, "T-6h": 6, "T-1h": 1}
    if named_cutoff not in mapping:
        raise ValueError(f"Unknown named cutoff {named_cutoff!r}; expected one of {list(mapping)}")
    if named_cutoff == "kickoff":
        return commence_time  # exact original string, no reformatting -- avoids any subtle mismatch risk
    hours_before = mapping[named_cutoff]
    resolved = kickoff - timedelta(hours=hours_before)
    return resolved.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _get_game_commence_time(conn, canonical_game_id):
    row = conn.execute(
        "SELECT commence_time FROM games_canonical WHERE canonical_game_id = ?", (canonical_game_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"canonical_game_id {canonical_game_id} not found in games_canonical")
    return row[0]


def _assert_cutoff_not_after_kickoff(conn, canonical_game_id, point_in_time_cutoff):
    commence_time = _get_game_commence_time(conn, canonical_game_id)
    if _parse_iso(point_in_time_cutoff) > _parse_iso(commence_time):
        raise PostKickoffCutoffError(
            f"point_in_time_cutoff {point_in_time_cutoff!r} is after game {canonical_game_id}'s "
            f"commence_time {commence_time!r}. The normal V2 feature interface refuses this "
            f"structurally -- a deliberate, separate diagnostic tool would be required to "
            f"inspect post-kickoff market state, not this function."
        )
    return commence_time


def market_state_as_of(conn, canonical_game_id, point_in_time_cutoff: str, book: str):
    """
    Returns a dict with home_spread, home_price, away_spread, away_price,
    favorite_side, spread_magnitude, market_observation_timestamp,
    staleness_seconds, source_raw_snapshot_id -- or None if no legitimate
    pregame observation exists for this book at or before the cutoff.

    book is REQUIRED. No default, no internal book-selection logic.
    """
    _assert_cutoff_not_after_kickoff(conn, canonical_game_id, point_in_time_cutoff)

    snapshot_columns = {row[1] for row in conn.execute("PRAGMA table_info(market_snapshots)").fetchall()}
    totals_select = (
        "total_points, total_price_over, total_price_under"
        if {"total_points", "total_price_over", "total_price_under"}.issubset(snapshot_columns)
        else "NULL AS total_points, NULL AS total_price_over, NULL AS total_price_under"
    )
    rows = conn.execute(
        "SELECT snapshot_id, observed_at, book_last_update, spread_home, spread_away, "
        "spread_price_home, spread_price_away, " + totals_select + " FROM market_snapshots "
        "WHERE canonical_game_id = ? AND book = ? ORDER BY observed_at ASC",
        (canonical_game_id, book),
    ).fetchall()

    cutoff_dt = _parse_iso(point_in_time_cutoff)
    best = None
    best_effective_dt = None
    for row in rows:
        snapshot_id, observed_at, book_last_update, spread_home, spread_away, price_home, price_away, total_points, total_price_over, total_price_under = row
        effective_time = book_last_update if book_last_update else observed_at
        effective_dt = _parse_iso(effective_time)
        if effective_dt <= cutoff_dt:
            if best_effective_dt is None or effective_dt > best_effective_dt:
                best = row
                best_effective_dt = effective_dt

    if best is None:
        return None

    snapshot_id, observed_at, book_last_update, spread_home, spread_away, price_home, price_away, total_points, total_price_over, total_price_under = best
    effective_time = book_last_update if book_last_update else observed_at

    favorite_side = None
    spread_magnitude = None
    if spread_home is not None:
        spread_magnitude = abs(spread_home)
        if spread_home < 0:
            favorite_side = "home"
        elif spread_home > 0:
            favorite_side = "away"
        else:
            favorite_side = "pick_em"

    staleness_seconds = (cutoff_dt - best_effective_dt).total_seconds()

    return {
        "home_spread": spread_home, "home_price": price_home,
        "away_spread": spread_away, "away_price": price_away,
        "total_points": total_points, "total_price_over": total_price_over,
        "total_price_under": total_price_under,
        "favorite_side": favorite_side, "spread_magnitude": spread_magnitude,
        "market_observation_timestamp": effective_time,
        "staleness_seconds": staleness_seconds,
        "source_raw_snapshot_id": snapshot_id,
    }


def persist_market_state_as_of(conn, canonical_game_id, point_in_time_cutoff, book) -> str:
    """
    Orchestration wrapper, separated from the pure computation above.
    Option-C idempotency: a row already existing for
    (canonical_game_id, point_in_time_cutoff, book) is a no-op, not a
    duplicate or overwrite (append-only).
    """
    existing = conn.execute(
        "SELECT state_id FROM market_state_by_cutoff "
        "WHERE canonical_game_id = ? AND point_in_time_cutoff = ? AND book = ?",
        (canonical_game_id, point_in_time_cutoff, book),
    ).fetchone()
    if existing is not None:
        return "unchanged"

    state = market_state_as_of(conn, canonical_game_id, point_in_time_cutoff, book)
    computed_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    if state is None:
        conn.execute(
            "INSERT INTO market_state_by_cutoff (canonical_game_id, point_in_time_cutoff, book, computed_at) "
            "VALUES (?, ?, ?, ?)",
            (canonical_game_id, point_in_time_cutoff, book, computed_at),
        )
        conn.commit()
        return "new_null"

    conn.execute(
        "INSERT INTO market_state_by_cutoff (canonical_game_id, point_in_time_cutoff, book, computed_at, "
        "home_spread, home_price, away_spread, away_price, favorite_side, spread_magnitude, "
        "market_observation_timestamp, staleness_seconds, source_raw_snapshot_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (canonical_game_id, point_in_time_cutoff, book, computed_at,
         state["home_spread"], state["home_price"], state["away_spread"], state["away_price"],
         state["favorite_side"], state["spread_magnitude"],
         state["market_observation_timestamp"], state["staleness_seconds"], state["source_raw_snapshot_id"]),
    )
    conn.commit()
    return "new"
