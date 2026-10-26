import json
import sqlite3
from datetime import datetime, timezone

from book_need.inference import compute_inference
from book_need.signal_line import get_representative_signal_line


SIGNAL_VERSION = "mixed_book_side_dominant_v1"


def _utc_now():
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _current_segment(inference):
    current_name = inference.get("current_segment_name")

    return next(
        (
            segment
            for segment in inference.get("segments", [])
            if segment.get("name") == current_name
        ),
        {},
    )


def evaluate_experimental_mixed_signal(
    conn,
    canonical_game_id,
    sport,
    commence_time,
    as_of,
):
    """
    Evaluate the frozen experimental MIXED/book-side-dominant v1 rule.

    This function does not write to the database.
    It does not inspect game results.
    """

    inference = compute_inference(
        conn,
        canonical_game_id,
        as_of,
    )

    public_pressure = inference.get("public_pressure") or {}

    public_side = public_pressure.get(
        "side_preference_direction"
    )
    sp_percentile = public_pressure.get(
        "side_preference_magnitude_percentile"
    )

    if public_side not in ("home", "away"):
        return None

    if sp_percentile is None or sp_percentile <= 50:
        return None

    current = _current_segment(inference)

    if current.get("aggregate_regime") != "MIXED":
        return None

    counts = {
        "RESISTANCE": 0,
        "DIVERGENCE": 0,
        "PARTIAL_DIVERGENCE": 0,
        "CONFIRMATION": 0,
        "NO_MOVEMENT": 0,
    }

    for data in current.get("per_book", {}).values():
        regime = data.get("regime")

        if regime in counts:
            counts[regime] += 1

    book_side_count = (
        counts["RESISTANCE"]
        + counts["DIVERGENCE"]
        + counts["PARTIAL_DIVERGENCE"]
    )

    confirmation_count = counts["CONFIRMATION"]

    if book_side_count <= confirmation_count:
        return None

    experimental_side = (
        "away"
        if public_side == "home"
        else "home"
    )

    signal_line = get_representative_signal_line(
        conn,
        canonical_game_id,
        as_of,
        experimental_side,
        commence_time=commence_time,
    )

    line_value = None
    line_method = None

    if signal_line:
        line_value = signal_line.get("spread")
        line_method = signal_line.get("method")

    return {
        "canonical_game_id": canonical_game_id,
        "sport": sport,
        "signal_version": SIGNAL_VERSION,
        "as_of": as_of,
        "public_side": public_side,
        "arbitrage_side": experimental_side,
        "side_preference_percentile": sp_percentile,
        "aggregate_regime": "MIXED",
        "resistance_count": counts["RESISTANCE"],
        "divergence_count": counts["DIVERGENCE"],
        "partial_divergence_count": counts[
            "PARTIAL_DIVERGENCE"
        ],
        "confirmation_count": counts["CONFIRMATION"],
        "no_movement_count": counts["NO_MOVEMENT"],
        "book_side_count": book_side_count,
        "signal_line": line_value,
        "signal_line_method": line_method,
        "data_quality": inference.get("data_quality"),
        "full_record": {
            "experimental_signal": {
                "definition": (
                    "SP>50 AND aggregate=MIXED AND "
                    "(RESISTANCE+DIVERGENCE+"
                    "PARTIAL_DIVERGENCE)>CONFIRMATION"
                ),
                "side": experimental_side,
                "signal_version": SIGNAL_VERSION,
            },
            "inference": inference,
            "signal_line": signal_line,
        },
    }


def record_experimental_mixed_signal(
    conn,
    canonical_game_id,
    sport,
    commence_time,
    as_of,
):
    """
    Evaluate and prospectively persist one immutable v1 signal.

    Existing signals are never replaced or updated.
    """

    signal = evaluate_experimental_mixed_signal(
        conn,
        canonical_game_id,
        sport,
        commence_time,
        as_of,
    )

    if signal is None:
        return None

    written_at = _utc_now()

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO experimental_mixed_signals (
            canonical_game_id,
            sport,
            signal_version,
            as_of,
            written_at,
            public_side,
            arbitrage_side,
            side_preference_percentile,
            aggregate_regime,
            resistance_count,
            divergence_count,
            partial_divergence_count,
            confirmation_count,
            no_movement_count,
            book_side_count,
            signal_line,
            signal_line_method,
            data_quality,
            full_record_json
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            signal["canonical_game_id"],
            signal["sport"],
            signal["signal_version"],
            signal["as_of"],
            written_at,
            signal["public_side"],
            signal["arbitrage_side"],
            signal["side_preference_percentile"],
            signal["aggregate_regime"],
            signal["resistance_count"],
            signal["divergence_count"],
            signal["partial_divergence_count"],
            signal["confirmation_count"],
            signal["no_movement_count"],
            signal["book_side_count"],
            signal["signal_line"],
            signal["signal_line_method"],
            signal["data_quality"],
            json.dumps(signal["full_record"]),
        ),
    )

    if cursor.rowcount == 0:
        return None

    conn.commit()

    signal["written_at"] = written_at

    return signal
