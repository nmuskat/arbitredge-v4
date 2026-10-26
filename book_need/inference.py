"""
book_need/inference.py

Orchestrates one complete Inferred Book Need computation for one game at
one as_of timestamp -- the full locked pipeline, per-book-first,
aggregate-second, no sportsbook weighting, no probabilities.
"""

from book_need.segments import resolve_segments, is_stale, NOT_YET_OBSERVED
from book_need.public_pressure import compute_public_pressure
from book_need.regime import compute_per_book_response
from book_need.aggregation import aggregate_book_regimes, select_current_segment, check_prior_contradiction
from book_need.need_strength import infer_book_need, apply_modifiers_and_cap, compute_data_quality
from book_need.synthetic_sportsbook import evaluate_synthetic_book
from book_need.signal_line import get_representative_signal_line
from features.market_state_as_of import market_state_as_of, PostKickoffCutoffError, _parse_iso
from reporting.checkpoint_reconstruction import get_books_for_game

MODEL_VERSION = "book_need_week1_v1"


def _get_game_row(conn, canonical_game_id):
    return conn.execute(
        "SELECT sport, home_team_id, away_team_id, commence_time FROM games_canonical WHERE canonical_game_id=?",
        (canonical_game_id,),
    ).fetchone()


def _get_raw_spread_sequence(conn, canonical_game_id, book, start, end):
    rows = conn.execute(
        "SELECT observed_at, book_last_update, spread_home FROM market_snapshots "
        "WHERE canonical_game_id=? AND book=? ORDER BY observed_at ASC",
        (canonical_game_id, book),
    ).fetchall()
    start_dt, end_dt = _parse_iso(start), _parse_iso(end)
    seq = []
    for observed_at, book_last_update, spread_home in rows:
        effective = book_last_update if book_last_update else observed_at
        eff_dt = _parse_iso(effective)
        if start_dt <= eff_dt <= end_dt:
            seq.append(spread_home)
    return seq


def compute_inference(conn, canonical_game_id, as_of, model_version=MODEL_VERSION, synthetic_bettor_ledger=None):
    game_row = _get_game_row(conn, canonical_game_id)
    if game_row is None:
        return None
    sport, home, away, commence_time = game_row

    books = get_books_for_game(conn, canonical_game_id)
    segments = resolve_segments(commence_time, as_of)

    for seg in segments:
        seg["aggregate_regime"] = None
        seg["breadth"] = None
        seg["per_book"] = {}
        seg["public_pressure"] = None

        if seg["status"] == NOT_YET_OBSERVED:
            continue

        pp = compute_public_pressure(
            conn,
            canonical_game_id,
            sport,
            commence_time,
            seg["start_label"],
            hi_as_of=seg["evaluation_horizon"],
        )
        seg["public_pressure"] = pp
        if pp is None:
            continue

        pressure_side_for_S = pp["side_preference_direction"] if pp["side_preference_direction"] in ("home", "away") else "home"

        book_regimes = []
        for book in books:
            try:
                start_state = market_state_as_of(conn, canonical_game_id, seg["start"], book)
                end_state = market_state_as_of(conn, canonical_game_id, seg["evaluation_horizon"], book)
            except PostKickoffCutoffError:
                seg["per_book"][book] = {"regime": "INSUFFICIENT", "reason": "post_kickoff_cutoff"}
                continue

            if start_state is None or end_state is None:
                seg["per_book"][book] = {"regime": "INSUFFICIENT", "reason": "missing_observation"}
                continue

            stale = is_stale(commence_time, seg["evaluation_horizon"], end_state["market_observation_timestamp"])
            if stale:
                seg["per_book"][book] = {"regime": "INSUFFICIENT", "reason": "stale"}
                continue

            pressured_price_start = start_state["home_price"] if pressure_side_for_S == "home" else start_state["away_price"]
            pressured_price_end = end_state["home_price"] if pressure_side_for_S == "home" else end_state["away_price"]
            raw_seq = _get_raw_spread_sequence(conn, canonical_game_id, book, seg["start"], seg["evaluation_horizon"])

            response = compute_per_book_response(
                pressure_side_for_S, start_state["home_spread"], end_state["home_spread"],
                pressured_price_start, pressured_price_end, raw_spread_sequence=raw_seq,
            )
            seg["per_book"][book] = response
            book_regimes.append(response["regime"])

        agg_regime, breadth = aggregate_book_regimes(book_regimes)
        seg["aggregate_regime"] = agg_regime
        seg["breadth"] = breadth

    current_segment, fallback_occurred = select_current_segment(segments)
    nothing_attempted = current_segment is None and not fallback_occurred

    usable_book_count = 0
    any_stale = False
    key_number_states = []
    if current_segment is not None:
        usable_book_count = sum(1 for r in current_segment["per_book"].values() if r["regime"] != "INSUFFICIENT")
        any_stale = any(r.get("reason") == "stale" for r in current_segment["per_book"].values())
        key_number_states = [r.get("key_number_state") for r in current_segment["per_book"].values()
                              if r.get("regime") != "INSUFFICIENT"]

    public_pressure = current_segment["public_pressure"] if current_segment else None

    side, base_strength = infer_book_need(public_pressure, current_segment, usable_book_count, nothing_attempted)

    synthetic_sportsbook = evaluate_synthetic_book(
        public_pressure,
        current_segment,
        usable_book_count,
        canonical_game_id=canonical_game_id,
        synthetic_ledger=synthetic_bettor_ledger,
    )

    contradiction_flag = False
    if current_segment is not None:
        contradiction_flag = check_prior_contradiction(current_segment, segments)

    need_strength = None
    if side not in ("NOT_YET_EVALUABLE", "NO_CLEAR_NEED", "INSUFFICIENT_EVIDENCE"):
        need_strength = apply_modifiers_and_cap(
            base_strength, current_segment.get("breadth"), contradiction_flag,
            current_segment.get("aggregate_regime"), key_number_states, public_pressure,
        )

    data_quality = compute_data_quality(
        usable_book_count, any_stale,
        public_pressure["handle_intensity_coverage_count"] if public_pressure else None,
        public_pressure["side_preference_coverage_count"] if public_pressure else None,
        fallback_occurred, nothing_attempted,
    )

    return {
        "canonical_game_id": canonical_game_id, "sport": sport,
        "home_team_id": home, "away_team_id": away, "commence_time": commence_time,
        "as_of": as_of, "model_version": model_version,
        "segments": segments,
        "current_segment_name": current_segment["name"] if current_segment else None,
        "fallback_occurred": fallback_occurred,
        "nothing_attempted": nothing_attempted,
        "earliest_evaluation_window_start": segments[0]["start"],
        "contradiction_flag": contradiction_flag,
        "public_pressure": public_pressure,
        "synthetic_sportsbook": synthetic_sportsbook,
        "inferred_book_need_side": side,
        "signal_line": get_representative_signal_line(
            conn,
            canonical_game_id,
            as_of,
            side,
            commence_time=commence_time,
        ),
        "need_strength": need_strength,
        "data_quality": data_quality,
    }
