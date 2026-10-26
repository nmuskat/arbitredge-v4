"""
reporting/checkpoint_reconstruction.py

Pure reporting/query logic over the raw, untouched market_snapshots time
series. Writes nothing. Never persists a "checkpoint" as if it were an
observation -- every checkpoint is recomputed at report time from the raw
data, which stays exactly as collected.

TERMINOLOGY, per instruction: "earliest_observed" and
"last_observed_before_kickoff" are used deliberately instead of "opening
line"/"closing line" -- neither term is used anywhere in this module.

POINT-IN-TIME RULE, hard and non-negotiable: a checkpoint is NEVER filled
by an observation at or after kickoff. Enforced structurally below (the
pregame filter), not by convention.

TOLERANCES are explicit and tied to the actual polling cadence built in
operations/cadence_config.py, not arbitrary round numbers:
  - T-24h sits at the boundary between the 72h-24h tier (every 16h) and
    the 24h-6h tier (every 5h) -- tolerance of +/-8h is generous enough to
    catch a real observation from either tier without accepting something
    misleadingly far off.
  - T-6h sits at the boundary between the 24h-6h tier (every 5h) and the
    6h-1h tier (every 1h) -- tolerance +/-2.5h.
  - T-1h sits inside the 6h-1h/<1h tiers (every 1h, then every 20min) --
    tolerance +/-0.5h.
  - last_observed_before_kickoff additionally requires the closest
    pregame observation to be within LAST_OBSERVED_MAX_LOOKBACK_HOURS of
    kickoff, so a stale, far-off last observation is never mislabeled as
    if it were close to kickoff.

Missing data is always None, never interpolated or fabricated.
"""

from datetime import datetime, timedelta

CHECKPOINT_TARGET_HOURS = {"T-24h": 24, "T-6h": 6, "T-1h": 1}
CHECKPOINT_TOLERANCE_HOURS = {"T-24h": 8, "T-6h": 2.5, "T-1h": 0.5}
LAST_OBSERVED_MAX_LOOKBACK_HOURS = 3
ALL_CHECKPOINT_NAMES = ["earliest_observed", "T-24h", "T-6h", "T-1h", "last_observed_before_kickoff"]


def _parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _effective_time(row):
    return row["book_last_update"] if row.get("book_last_update") else row["observed_at"]


def get_books_for_game(conn, canonical_game_id):
    rows = conn.execute(
        "SELECT DISTINCT book FROM market_snapshots WHERE canonical_game_id = ? ORDER BY book",
        (canonical_game_id,),
    ).fetchall()
    return [r[0] for r in rows]


def get_book_snapshots(conn, canonical_game_id, book):
    rows = conn.execute(
        "SELECT observed_at, book_last_update, spread_home, spread_away, "
        "spread_price_home, spread_price_away FROM market_snapshots "
        "WHERE canonical_game_id = ? AND book = ? ORDER BY observed_at ASC",
        (canonical_game_id, book),
    ).fetchall()
    return [
        {
            "observed_at": r[0], "book_last_update": r[1],
            "spread_home": r[2], "spread_away": r[3],
            "spread_price_home": r[4], "spread_price_away": r[5],
        }
        for r in rows
    ]


def reconstruct_checkpoints_for_book(canonical_game_id, kickoff_iso, book, snapshots):
    """
    Pure function -- takes already-fetched snapshots so this is directly
    unit-testable without a live DB connection. Returns
    {checkpoint_name: snapshot_dict_or_None, ...} for exactly the 5 named
    checkpoints.
    """
    kickoff = _parse_iso(kickoff_iso)
    pregame = [s for s in snapshots if _parse_iso(_effective_time(s)) < kickoff]

    result = {name: None for name in ALL_CHECKPOINT_NAMES}
    if not pregame:
        return result

    earliest = min(pregame, key=lambda s: _effective_time(s))
    result["earliest_observed"] = earliest

    last = max(pregame, key=lambda s: _effective_time(s))
    hours_before_kickoff = (kickoff - _parse_iso(_effective_time(last))).total_seconds() / 3600
    if hours_before_kickoff <= LAST_OBSERVED_MAX_LOOKBACK_HOURS:
        result["last_observed_before_kickoff"] = last

    for name, target_hours in CHECKPOINT_TARGET_HOURS.items():
        target_time = kickoff - timedelta(hours=target_hours)
        tolerance = CHECKPOINT_TOLERANCE_HOURS[name]
        best, best_diff = None, None
        for s in pregame:
            diff_hours = abs((_parse_iso(_effective_time(s)) - target_time).total_seconds() / 3600)
            if diff_hours <= tolerance and (best_diff is None or diff_hours < best_diff):
                best, best_diff = s, diff_hours
        result[name] = best

    return result


def compute_movement(checkpoints):
    """Movement between consecutive AVAILABLE checkpoints (None entries
    skipped), preserving the spread-number-vs-price-only distinction.
    Consecutive checkpoints that resolve to the SAME underlying
    observation (e.g. a book with only one real snapshot, which multiple
    checkpoint names correctly point back to) are collapsed -- there is
    no real movement to report between an observation and itself."""
    ordered = [checkpoints[name] for name in ALL_CHECKPOINT_NAMES if checkpoints[name] is not None]
    movements = []
    for prev, curr in zip(ordered, ordered[1:]):
        if prev["observed_at"] == curr["observed_at"]:
            continue  # same underlying observation, not a real movement
        spread_delta = None
        if prev["spread_home"] is not None and curr["spread_home"] is not None:
            spread_delta = round(curr["spread_home"] - prev["spread_home"], 2)
        price_delta = None
        if prev["spread_price_home"] is not None and curr["spread_price_home"] is not None:
            price_delta = round(curr["spread_price_home"] - prev["spread_price_home"], 2)
        movements.append({
            "from": prev["observed_at"], "to": curr["observed_at"],
            "spread_home_delta": spread_delta,
            "price_only_move": (spread_delta == 0 and price_delta not in (None, 0)),
            "spread_price_home_delta": price_delta,
        })
    return movements


def compute_cross_book_disagreement(conn, canonical_game_id, kickoff_iso):
    """At each named checkpoint, compares spread_home across every book
    with a valid observation there. Returns raw per-book values and the
    simple range (max-min) -- NOT a consensus value."""
    books = get_books_for_game(conn, canonical_game_id)
    per_checkpoint = {name: {} for name in ALL_CHECKPOINT_NAMES}

    for book in books:
        snapshots = get_book_snapshots(conn, canonical_game_id, book)
        checkpoints = reconstruct_checkpoints_for_book(canonical_game_id, kickoff_iso, book, snapshots)
        for name, snap in checkpoints.items():
            if snap is not None and snap["spread_home"] is not None:
                per_checkpoint[name][book] = snap["spread_home"]

    disagreement = {}
    for name, book_values in per_checkpoint.items():
        if len(book_values) >= 2:
            values = list(book_values.values())
            disagreement[name] = {"per_book": book_values, "range": round(max(values) - min(values), 2)}
        else:
            disagreement[name] = {"per_book": book_values, "range": None}
    return disagreement
