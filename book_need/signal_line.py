"""
book_need/signal_line.py

Point-in-time execution-line support for an already-computed ArbitrEdge side.

This module does NOT participate in inference. It only retrieves sportsbook
spread states legitimately known at or before the supplied as_of timestamp.

No future observations, interpolation, sportsbook weighting, or fabricated
lines are permitted.
"""

from features.market_state_as_of import market_state_as_of
from reporting.checkpoint_reconstruction import get_books_for_game
from book_need.segments import is_stale


NON_DIRECTIONAL_SIDES = {
    "NOT_YET_EVALUABLE",
    "NO_CLEAR_NEED",
    "INSUFFICIENT_EVIDENCE",
}


def get_available_signal_lines(conn, canonical_game_id, as_of, inferred_side, commence_time=None):
    """
    Return the available point-in-time spread for every book for the already
    inferred home/away side.

    This function does not select a representative line and does not write
    anything to the database.
    """
    if inferred_side in NON_DIRECTIONAL_SIDES:
        return []

    if inferred_side not in ("home", "away"):
        raise ValueError(
            f"inferred_side must be 'home', 'away', or a recognized "
            f"non-directional state; got {inferred_side!r}"
        )

    lines = []

    for book in get_books_for_game(conn, canonical_game_id):
        state = market_state_as_of(conn, canonical_game_id, as_of, book)

        if state is None:
            continue

        if commence_time is not None and is_stale(
            commence_time,
            as_of,
            state["market_observation_timestamp"],
        ):
            continue

        spread = (
            state["home_spread"]
            if inferred_side == "home"
            else state["away_spread"]
        )
        price = (
            state["home_price"]
            if inferred_side == "home"
            else state["away_price"]
        )

        if spread is None:
            continue

        lines.append(
            {
                "book": book,
                "side": inferred_side,
                "spread": spread,
                "price": price,
                "market_observation_timestamp": state[
                    "market_observation_timestamp"
                ],
                "staleness_seconds": state["staleness_seconds"],
                "source_raw_snapshot_id": state["source_raw_snapshot_id"],
            }
        )

    return lines


def select_representative_signal_line(lines):
    """
    Select a representative spread from already-retrieved point-in-time
    sportsbook lines.

    The representative spread is the median of all valid book spreads.
    No sportsbook weighting is applied.

    Returns None when no valid lines are available.
    """
    if not lines:
        return None

    ordered_spreads = sorted(float(line["spread"]) for line in lines)
    count = len(ordered_spreads)
    midpoint = count // 2

    if count % 2 == 1:
        representative_spread = ordered_spreads[midpoint]
    else:
        representative_spread = ordered_spreads[midpoint - 1]

    matching_lines = [
        line for line in lines
        if float(line["spread"]) == representative_spread
    ]

    return {
        "spread": representative_spread,
        "book_count": count,
        "min_spread": min(ordered_spreads),
        "max_spread": max(ordered_spreads),
        "matching_book_count": len(matching_lines),
        "method": "observed_lower_median_unweighted_available_books",
    }


def get_representative_signal_line(
    conn, canonical_game_id, as_of, inferred_side, commence_time=None
):
    """
    Convenience wrapper: retrieve legitimate point-in-time book lines and
    select their representative spread.
    """
    lines = get_available_signal_lines(
        conn,
        canonical_game_id,
        as_of,
        inferred_side,
        commence_time=commence_time,
    )

    representative = select_representative_signal_line(lines)

    if representative is None:
        return None

    return {
        "side": inferred_side,
        "as_of": as_of,
        **representative,
        "lines": lines,
    }
