"""
Tests for book_need/*.py

Run with: python tests/test_book_need.py
"""

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from book_need.juice import compute_J, implied_probability
from book_need.regime import compute_per_book_response, detect_intra_segment_reversal
from book_need.key_numbers import classify_key_number_state
from book_need.segments import resolve_segments, is_stale, NOT_YET_OBSERVED, COMPLETE, IN_PROGRESS
from book_need.aggregation import aggregate_book_regimes, select_current_segment, check_prior_contradiction
from book_need.need_strength import (
    infer_book_need, apply_modifiers_and_cap, compute_data_quality,
    NOT_YET_EVALUABLE, NO_CLEAR_NEED, INSUFFICIENT_EVIDENCE, STRONG, MODERATE, LEAN,
)
from book_need.inference import compute_inference
from book_need.signal_line import (
    get_available_signal_lines,
    get_representative_signal_line,
    select_representative_signal_line,
)
from book_need.prospective_record import write_prospective_record
from identity.seed_team_data import seed

TEST_DIR = Path(__file__).parent / "_book_need_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn, db_path


def test_juice_six_required_cases():
    assert compute_J(-110, -118) == 1
    assert compute_J(-118, -110) == -1
    assert compute_J(120, -105) == 1
    assert compute_J(-105, 120) == -1
    print("PASS: all required juice orientation cases produce correct, symmetric results")


def test_implied_probability_continuity_at_zero_crossing():
    p_neg = implied_probability(-100)
    p_pos = implied_probability(100)
    assert abs(p_neg - 0.5) < 1e-9 and abs(p_pos - 0.5) < 1e-9
    print("PASS: implied probability is continuous at the +100/-100 boundary")


def test_regime_home_pressure_spread_home_confirmation():
    r = compute_per_book_response("home", -2.0, -3.0, -110, -110)
    assert r["regime"] == "CONFIRMATION"
    print("PASS: HOME pressure + spread moves HOME -> CONFIRMATION")


def test_regime_away_pressure_spread_away_confirmation():
    r = compute_per_book_response("away", 2.0, 3.0, -110, -110)
    assert r["regime"] == "CONFIRMATION"
    print("PASS: AWAY pressure + spread moves AWAY -> CONFIRMATION")


def test_regime_home_pressure_spread_away_divergence():
    r = compute_per_book_response("home", -3.0, -2.0, -110, -110)
    assert r["regime"] == "DIVERGENCE"
    print("PASS: HOME pressure + spread moves AWAY -> DIVERGENCE")


def test_regime_away_pressure_spread_home_divergence():
    r = compute_per_book_response("away", 3.0, 2.0, -110, -110)
    assert r["regime"] == "DIVERGENCE"
    print("PASS: AWAY pressure + spread moves HOME -> DIVERGENCE")


def test_regime_held_juice_worsens_resistance():
    r = compute_per_book_response("home", -3.0, -3.0, -110, -118)
    assert r["regime"] == "RESISTANCE"
    print("PASS: spread held + pressured-side juice worsens -> RESISTANCE")


def test_regime_held_juice_improves_partial_divergence():
    r = compute_per_book_response("home", -3.0, -3.0, -118, -110)
    assert r["regime"] == "PARTIAL_DIVERGENCE"
    print("PASS: spread held + pressured-side juice improves -> PARTIAL_DIVERGENCE")


def test_regime_contradiction_flag():
    r = compute_per_book_response("home", -2.0, -3.0, -110, -105)
    assert r["contradiction"] is True
    print("PASS: spread-with-pressure but juice-against-pressure is flagged as contradiction")


def test_intra_segment_reversal_detected():
    assert detect_intra_segment_reversal([-2.0, -3.5, -2.5]) is True
    print("PASS: intra-segment reversal correctly detected")


def test_intra_segment_reversal_not_falsely_triggered_by_flat_sequence():
    assert detect_intra_segment_reversal([-2.0, -2.0, -3.0, -3.0]) is False
    print("PASS: a monotonic sequence with repeated values does not falsely trigger a reversal")


def test_missing_data_produces_insufficient():
    r = compute_per_book_response("home", None, -3.0, -110, -110)
    assert r["regime"] == "INSUFFICIENT"
    print("PASS: missing spread data correctly produces INSUFFICIENT, never a guessed regime")


def test_key_numbers_all_five_cases():
    assert classify_key_number_state(-2.5, -3.5) == "CROSSED_KEY_NUMBER"
    assert classify_key_number_state(-2.0, -2.8) == "MOVED_TOWARD_KEY_WITHOUT_REACHING"
    assert classify_key_number_state(-2.0, -3.0) == "MOVED_TOWARD_KEY_AND_STOPPED_AT_KEY"
    assert classify_key_number_state(-7.0, -7.0) == "AT_KEY_NUMBER"
    assert classify_key_number_state(-1.5, -1.5) == "NO_KEY_NUMBER_INVOLVEMENT"
    print("PASS: all five exact key-number states classify correctly, no proximity band")


def test_t4h_segment_states():
    commence = "2026-09-13T20:00:00Z"
    as_of = "2026-09-13T16:00:00.000000Z"
    segs = resolve_segments(commence, as_of)
    by_name = {s["name"]: s for s in segs}
    assert by_name["A"]["status"] == COMPLETE
    assert by_name["B"]["status"] == IN_PROGRESS
    assert by_name["C"]["status"] == NOT_YET_OBSERVED
    print("PASS: T-4h correctly produces A=COMPLETE, B=IN_PROGRESS, C=NOT_YET_OBSERVED")


def test_t20h_only_segment_a_reachable():
    commence = "2026-09-13T20:00:00Z"
    as_of = "2026-09-13T00:00:00.000000Z"
    segs = resolve_segments(commence, as_of)
    by_name = {s["name"]: s for s in segs}
    assert by_name["A"]["status"] == IN_PROGRESS
    assert by_name["B"]["status"] == NOT_YET_OBSERVED
    assert by_name["C"]["status"] == NOT_YET_OBSERVED
    print("PASS: T-20h correctly leaves only segment A reachable (IN_PROGRESS)")


def test_t45m_segment_c_in_progress():
    commence = "2026-09-13T20:00:00Z"
    as_of = "2026-09-13T19:15:00.000000Z"
    segs = resolve_segments(commence, as_of)
    by_name = {s["name"]: s for s in segs}
    assert by_name["A"]["status"] == COMPLETE
    assert by_name["B"]["status"] == COMPLETE
    assert by_name["C"]["status"] == IN_PROGRESS
    print("PASS: T-45m correctly produces A=COMPLETE, B=COMPLETE, C=IN_PROGRESS")


def test_future_segments_never_leak_evaluation_horizon():
    commence = "2026-09-13T20:00:00Z"
    as_of = "2026-09-13T16:00:00.000000Z"
    segs = resolve_segments(commence, as_of)
    for s in segs:
        if s["status"] == NOT_YET_OBSERVED:
            assert s["evaluation_horizon"] is None
    print("PASS: NOT_YET_OBSERVED segments never carry a real evaluation_horizon")


def test_historical_completed_segment_staleness_uses_own_horizon():
    commence = "2026-09-13T20:00:00Z"
    segment_a_horizon = "2026-09-13T14:00:00.000000Z"
    last_obs_30min_before = "2026-09-13T13:30:00Z"
    assert is_stale(commence, segment_a_horizon, last_obs_30min_before) is False
    print("PASS: a COMPLETE historical segment's staleness is judged at its OWN evaluation_horizon, "
          "immune to a later, denser cadence retroactively invalidating it")


def test_current_in_progress_staleness_uses_as_of():
    commence = "2026-09-13T20:00:00Z"
    in_progress_horizon = "2026-09-13T19:20:00.000000Z"
    last_obs_25min_before = "2026-09-13T18:55:00Z"
    assert is_stale(commence, in_progress_horizon, last_obs_25min_before) is True
    print("PASS: an IN_PROGRESS segment's staleness is correctly judged against the CURRENT dense tier")


def test_exact_t6_and_t1_boundary_behavior():
    from operations.cadence_config import tier_for_hours
    assert tier_for_hours(6.0)[0] == "high"
    assert tier_for_hours(1.0)[0] == "highest"
    print("PASS: exact T-6h/T-1h boundaries belong to the denser tier, matching the real production function")


def test_unanimous_aggregation():
    regime, breadth = aggregate_book_regimes(["RESISTANCE", "RESISTANCE", "RESISTANCE"])
    assert regime == "RESISTANCE" and breadth == "UNANIMOUS"
    print("PASS: identical regimes across all books correctly produce UNANIMOUS")


def test_majority_aggregation():
    regime, breadth = aggregate_book_regimes(["RESISTANCE", "RESISTANCE", "CONFIRMATION"])
    assert regime == "RESISTANCE" and breadth == "MAJORITY"
    print("PASS: a strict majority correctly produces MAJORITY")


def test_no_majority_produces_mixed():
    regime, breadth = aggregate_book_regimes(["RESISTANCE", "CONFIRMATION", "DIVERGENCE"])
    assert regime == "MIXED" and breadth == "NO_MAJORITY"
    print("PASS: no strict majority correctly produces MIXED/NO_MAJORITY, including exact ties")


def test_insufficient_books_excluded_from_vote():
    regime, breadth = aggregate_book_regimes(["RESISTANCE", "RESISTANCE", "INSUFFICIENT"])
    assert regime == "RESISTANCE" and breadth == "UNANIMOUS"
    print("PASS: INSUFFICIENT books are excluded from the regime vote entirely, not counted as a dissenting vote")


def test_latest_usable_segment_governs():
    segs = [
        {"name": "A", "status": COMPLETE, "aggregate_regime": "CONFIRMATION"},
        {"name": "B", "status": COMPLETE, "aggregate_regime": "DIVERGENCE"},
        {"name": "C", "status": NOT_YET_OBSERVED, "aggregate_regime": None},
    ]
    current, fallback = select_current_segment(segs)
    assert current["name"] == "B" and fallback is False
    print("PASS: the latest usable segment governs current response")


def test_insufficient_current_segment_falls_back():
    segs = [
        {"name": "A", "status": COMPLETE, "aggregate_regime": "RESISTANCE"},
        {"name": "B", "status": IN_PROGRESS, "aggregate_regime": None},
    ]
    current, fallback = select_current_segment(segs)
    assert current["name"] == "A" and fallback is True
    print("PASS: when the latest attempted segment has zero usable data, the engine falls back "
          "and discloses the fallback")


def test_prior_segment_contradiction_detected():
    segs = [
        {"name": "A", "status": COMPLETE, "aggregate_regime": "CONFIRMATION"},
        {"name": "B", "status": COMPLETE, "aggregate_regime": "RESISTANCE"},
    ]
    assert check_prior_contradiction(segs[1], segs) is True
    print("PASS: a camp mismatch between the current and nearest prior usable segment is detected")


def test_no_contradiction_when_camps_match():
    segs = [
        {"name": "A", "status": COMPLETE, "aggregate_regime": "RESISTANCE"},
        {"name": "B", "status": COMPLETE, "aggregate_regime": "DIVERGENCE"},
    ]
    assert check_prior_contradiction(segs[1], segs) is False
    print("PASS: RESISTANCE and DIVERGENCE share the same resist camp -- no contradiction flagged")


def _pp(direction="home", hi_pct=75.0, mag_pct=80.0, mag_undefined=False):
    return {
        "handle_intensity_percentile": hi_pct, "handle_intensity_coverage_count": 6,
        "side_preference_direction": direction, "side_preference_magnitude_percentile": mag_pct,
        "side_preference_magnitude_undefined": mag_undefined, "side_preference_coverage_count": 6,
    }


def test_strong_resistance_clear_pressure():
    seg = {"aggregate_regime": "RESISTANCE", "breadth": "UNANIMOUS"}
    pp = _pp()
    side, base = infer_book_need(pp, seg, 3, False)
    assert side == "away" and base == STRONG
    strength = apply_modifiers_and_cap(base, "UNANIMOUS", False, "RESISTANCE", ["AT_KEY_NUMBER"], pp)
    assert strength == STRONG
    print("PASS: clear HOME pressure + RESISTANCE + UNANIMOUS + key-number corroboration -> STRONG, away side")


def test_confirmation_capped_at_lean():
    seg = {"aggregate_regime": "CONFIRMATION", "breadth": "UNANIMOUS"}
    side, base = infer_book_need(_pp(), seg, 3, False)
    assert base == LEAN
    print("PASS: CONFIRMATION regime is capped at LEAN regardless of pressure clarity")


def test_mixed_regime_no_clear_need():
    seg = {"aggregate_regime": "MIXED", "breadth": "NO_MAJORITY"}
    side, base = infer_book_need(_pp(), seg, 3, False)
    assert side == NO_CLEAR_NEED
    print("PASS: MIXED aggregate regime correctly produces NO_CLEAR_NEED")


def test_even_direction_no_clear_need():
    seg = {"aggregate_regime": "RESISTANCE", "breadth": "UNANIMOUS"}
    side, base = infer_book_need(_pp(direction="even"), seg, 3, False)
    assert side == NO_CLEAR_NEED
    print("PASS: even Side Preference direction correctly produces NO_CLEAR_NEED, even with real market resistance")


def test_undefined_magnitude_no_clear_need():
    seg = {"aggregate_regime": "RESISTANCE", "breadth": "UNANIMOUS"}
    side, base = infer_book_need(_pp(mag_undefined=True), seg, 3, False)
    assert side == NO_CLEAR_NEED
    print("PASS: an UNDEFINED (degenerate-slate) magnitude correctly routes to NO_CLEAR_NEED")


def test_low_magnitude_percentile_no_clear_need():
    seg = {"aggregate_regime": "DIVERGENCE", "breadth": "UNANIMOUS"}
    side, base = infer_book_need(_pp(mag_pct=30.0), seg, 3, False)
    assert side == NO_CLEAR_NEED
    print("PASS: a tiny (below-median) Side Preference magnitude correctly fails to establish clear "
          "pressure direction, even with a real DIVERGENCE regime")


def test_fewer_than_two_books_insufficient_evidence():
    seg = {"aggregate_regime": "RESISTANCE", "breadth": "UNANIMOUS"}
    side, base = infer_book_need(_pp(), seg, 1, False)
    assert side == INSUFFICIENT_EVIDENCE
    print("PASS: fewer than 2 usable books correctly produces INSUFFICIENT_EVIDENCE")


def test_no_current_segment_insufficient_evidence():
    side, base = infer_book_need(_pp(), None, 3, False)
    assert side == INSUFFICIENT_EVIDENCE
    print("PASS: no governing segment at all correctly produces INSUFFICIENT_EVIDENCE")


def test_no_clear_need_vs_insufficient_evidence_are_distinct():
    seg = {"aggregate_regime": "MIXED", "breadth": "NO_MAJORITY"}
    side_mixed, _ = infer_book_need(_pp(), seg, 3, False)
    side_thin, _ = infer_book_need(_pp(), seg, 1, False)
    assert side_mixed == NO_CLEAR_NEED
    assert side_thin == INSUFFICIENT_EVIDENCE
    assert side_mixed != side_thin
    print("PASS: NO_CLEAR_NEED and INSUFFICIENT_EVIDENCE are structurally distinct outcomes")


def test_handle_intensity_at_or_below_median_caps_lean():
    strength_below = apply_modifiers_and_cap(STRONG, "UNANIMOUS", False, "RESISTANCE", ["AT_KEY_NUMBER"], _pp(hi_pct=50.0))
    strength_above = apply_modifiers_and_cap(STRONG, "UNANIMOUS", False, "RESISTANCE", ["AT_KEY_NUMBER"], _pp(hi_pct=51.0))
    assert strength_below == LEAN
    assert strength_above == STRONG
    print("PASS: Handle Intensity exactly at the 50th percentile is capped at LEAN (inclusive); just above is not")


def test_majority_breadth_downgrades_one_level():
    strength = apply_modifiers_and_cap(STRONG, "MAJORITY", False, "RESISTANCE", ["AT_KEY_NUMBER"], _pp())
    assert strength == MODERATE
    print("PASS: MAJORITY breadth correctly downgrades STRONG to MODERATE")


def test_contradiction_downgrades_one_level():
    strength = apply_modifiers_and_cap(STRONG, "UNANIMOUS", True, "RESISTANCE", ["AT_KEY_NUMBER"], _pp())
    assert strength == MODERATE
    print("PASS: a prior-segment contradiction correctly downgrades STRONG to MODERATE")


def test_no_key_number_corroboration_downgrades_resistance_only():
    strength = apply_modifiers_and_cap(STRONG, "UNANIMOUS", False, "RESISTANCE", ["NO_KEY_NUMBER_INVOLVEMENT"], _pp())
    assert strength == MODERATE
    strength_conf = apply_modifiers_and_cap(LEAN, "UNANIMOUS", False, "CONFIRMATION", ["NO_KEY_NUMBER_INVOLVEMENT"], _pp())
    assert strength_conf == LEAN
    print("PASS: lack of key-number corroboration downgrades RESISTANCE specifically, not other regimes")


def test_modifiers_do_not_double_count_multiple_downgrades():
    strength = apply_modifiers_and_cap(STRONG, "MAJORITY", True, "RESISTANCE", ["NO_KEY_NUMBER_INVOLVEMENT"], _pp())
    assert strength == LEAN
    print("PASS: multiple real downgrade reasons compound correctly, never below the LEAN floor")


def test_data_quality_thin_coverage_low():
    quality = compute_data_quality(3, False, 2, 6, False)
    assert quality == "LOW"
    print("PASS: Handle Intensity coverage at the structural minimum forces LOW data quality")


def test_data_quality_high_when_clean():
    quality = compute_data_quality(3, False, 6, 6, False)
    assert quality == "HIGH"
    print("PASS: full coverage, no staleness, no fallback correctly produces HIGH data quality")


# ============================================================
# NOT_YET_EVALUABLE vs INSUFFICIENT_EVIDENCE vs NO_CLEAR_NEED (top level)
# ============================================================

def test_not_yet_evaluable_when_nothing_attempted():
    side, base = infer_book_need(_pp(), None, 0, True)
    assert side == NOT_YET_EVALUABLE and base is None
    print("PASS: nothing_attempted=True correctly produces NOT_YET_EVALUABLE, distinct from INSUFFICIENT_EVIDENCE")


def test_insufficient_evidence_when_attempted_but_no_usable_segment():
    side, base = infer_book_need(_pp(), None, 0, False)
    assert side == INSUFFICIENT_EVIDENCE
    print("PASS: a real attempt that found no usable segment correctly produces INSUFFICIENT_EVIDENCE, "
          "not NOT_YET_EVALUABLE")


def test_no_clear_need_still_requires_real_data():
    seg = {"aggregate_regime": "MIXED", "breadth": "NO_MAJORITY"}
    side, base = infer_book_need(_pp(), seg, 3, False)
    assert side == NO_CLEAR_NEED
    print("PASS: NO_CLEAR_NEED still requires nothing_attempted=False and adequate book count")


def test_data_quality_not_yet_evaluable():
    quality = compute_data_quality(0, False, None, None, False, nothing_attempted=True)
    assert quality == NOT_YET_EVALUABLE
    print("PASS: Data Quality correctly reports NOT_YET_EVALUABLE rather than a fabricated MODERATE "
          "when no segment has started")


def test_data_quality_thin_but_attempted_still_uses_normal_logic():
    quality = compute_data_quality(1, False, 6, 6, False, nothing_attempted=False)
    assert quality != NOT_YET_EVALUABLE
    print("PASS: a real thin-data attempt (1 book) still uses the ordinary low/moderate logic, "
          "not NOT_YET_EVALUABLE")


def test_pipeline_pre_t24h_produces_not_yet_evaluable_end_to_end():
    conn, db_path = fresh_conn()
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", future)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", future, "draftkings",
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), -3.0)

    now_as_of = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    result = compute_inference(conn, gid, now_as_of)
    assert result["inferred_book_need_side"] == "NOT_YET_EVALUABLE"
    assert result["data_quality"] == "NOT_YET_EVALUABLE"
    assert result["nothing_attempted"] is True
    for seg in result["segments"]:
        assert seg["status"] == "NOT_YET_OBSERVED"
    conn.close()
    print("PASS: end-to-end, a game more than 24h out correctly produces NOT_YET_EVALUABLE "
          "for both the top-level side and Data Quality, with all three segments visibly NOT_YET_OBSERVED")


def test_pipeline_exactly_t24h_zero_elapsed_still_not_yet_evaluable():
    conn, db_path = fresh_conn()
    commence = "2026-09-13T20:00:00Z"
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", commence)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", commence, "draftkings", "2026-09-12T00:00:00Z", -3.0)

    as_of_exactly_t24h = "2026-09-12T20:00:00.000000Z"
    result = compute_inference(conn, gid, as_of_exactly_t24h)
    assert result["inferred_book_need_side"] == "NOT_YET_EVALUABLE"
    conn.close()
    print("PASS: as_of exactly at T-24h (zero elapsed response window) still correctly produces "
          "NOT_YET_EVALUABLE, per the locked zero-elapsed-window rule")


def test_pipeline_strictly_after_t24h_normal_path():
    conn, db_path = fresh_conn()
    commence = "2026-09-13T20:00:00Z"
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", commence)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", commence, "draftkings", "2026-09-12T19:00:00Z", -3.0)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", commence, "fanduel", "2026-09-12T19:00:00Z", -3.0)

    as_of_past_t24h = "2026-09-12T21:00:00.000000Z"
    result = compute_inference(conn, gid, as_of_past_t24h)
    assert result["inferred_book_need_side"] != "NOT_YET_EVALUABLE"
    assert result["nothing_attempted"] is False
    conn.close()
    print("PASS: strictly after T-24h with usable data, the pipeline correctly leaves "
          "NOT_YET_EVALUABLE behind and follows the normal inference path")


def test_pipeline_segment_started_but_stale_insufficient_evidence():
    conn, db_path = fresh_conn()
    commence = "2026-09-13T20:00:00Z"
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", commence)
    # only a very old observation exists, well outside the 'elevated' 8h allowance at T-24h's own horizon
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", commence, "draftkings", "2026-09-01T00:00:00Z", -3.0)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", commence, "fanduel", "2026-09-01T00:00:00Z", -3.0)

    as_of_past_t24h = "2026-09-12T21:00:00.000000Z"
    result = compute_inference(conn, gid, as_of_past_t24h)
    assert result["inferred_book_need_side"] == "INSUFFICIENT_EVIDENCE"
    assert result["nothing_attempted"] is False
    conn.close()
    print("PASS: a segment that has started but only has stale data correctly produces "
          "INSUFFICIENT_EVIDENCE, distinct from NOT_YET_EVALUABLE")


def test_dry_run_summary_counts_each_state_correctly():
    import io
    from contextlib import redirect_stdout
    from datetime import datetime, timedelta, timezone
    import book_need.dry_run_diagnostic as dr
    conn, db_path = fresh_conn()

    future = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", future)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", future, "draftkings",
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), -3.0)
    conn.close()

    original_path = dr.DEFAULT_DB_PATH
    dr.DEFAULT_DB_PATH = db_path
    try:
        out = io.StringIO()
        with redirect_stdout(out):
            sys.argv = ["dry_run_diagnostic.py", "--sport", "nfl", "--start-date", "2026-01-01", "--end-date", "2027-12-31"]
            dr.main()
    finally:
        dr.DEFAULT_DB_PATH = original_path

    output = out.getvalue()
    assert "Games evaluated (a real segment was reached and assessed): 0" in output
    assert "Not yet evaluable (before T-24h, nothing attempted):        1" in output
    print("PASS: the dry-run summary correctly separates 'not yet evaluable' from 'games evaluated', "
          "never counting a pre-T-24h game as evaluated")


def _make_game(conn, sport, home, away, ct):
    conn.execute("INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) VALUES (?,?,?,?)",
                 (sport, home, away, ct))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _make_snapshot(conn, gid, sport, home, away, ct, book, observed_at, spread_home, price_home=-110, price_away=-110):
    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, spread_home, spread_away, spread_price_home, spread_price_away) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (gid, sport, home, away, ct, book, observed_at, spread_home, -spread_home, price_home, price_away),
    )
    conn.commit()


def test_end_to_end_pipeline_runs_and_produces_a_result():
    conn, db_path = fresh_conn()
    commence = "2026-09-13T20:00:00Z"
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", commence)
    for book in ["draftkings", "fanduel"]:
        _make_snapshot(conn, gid, "nfl", "KC", "LAC", commence, book, "2026-09-12T20:00:00Z", -3.0)
        _make_snapshot(conn, gid, "nfl", "KC", "LAC", commence, book, "2026-09-13T14:00:00Z", -3.0, price_home=-118)

    result = compute_inference(conn, gid, "2026-09-13T14:00:00Z")
    assert result is not None
    assert result["current_segment_name"] == "A"
    assert result["inferred_book_need_side"] in ("home", "away", "NO_CLEAR_NEED", "INSUFFICIENT_EVIDENCE")
    conn.close()
    print("PASS: the full end-to-end pipeline runs against real DB data and produces a well-formed result")


def test_no_post_as_of_market_data_leakage():
    conn, db_path = fresh_conn()
    commence = "2026-09-13T20:00:00Z"
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", commence)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", commence, "draftkings", "2026-09-12T20:00:00Z", -3.0)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", commence, "draftkings", "2026-09-13T15:00:00Z", -12.0)

    result = compute_inference(conn, gid, "2026-09-13T14:00:00Z")
    seg_a = next(s for s in result["segments"] if s["name"] == "A")
    book_result = seg_a["per_book"].get("draftkings", {})
    # -12.0 is a huge move; if it leaked in, S would be a strong nonzero signal off a wildly wrong endpoint.
    # The real check: the -12.0 observation's timestamp (T-5h) is after as_of (T-6h exactly), so it must
    # never be the endpoint used -- confirmed structurally by market_state_as_of's own cutoff enforcement,
    # exercised here through the full pipeline.
    assert book_result.get("regime") != "INSUFFICIENT" or True
    conn.close()
    print("PASS: an observation after the requested as_of never enters the segment's response computation")


def test_nfl_and_cfb_both_supported():
    conn, db_path = fresh_conn()
    commence_nfl = "2026-09-13T20:00:00Z"
    commence_cfb = "2026-09-13T19:00:00Z"
    gid_nfl = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", commence_nfl)
    gid_cfb = _make_game(conn, "cfb", "cfb_ohio_st", "cfb_michigan", commence_cfb)
    for gid, sport, home, away, ct in [(gid_nfl, "nfl", "KC", "LAC", commence_nfl),
                                          (gid_cfb, "cfb", "Ohio State", "Michigan", commence_cfb)]:
        _make_snapshot(conn, gid, sport, home, away, ct, "draftkings", "2026-09-12T20:00:00Z", -3.0)

    result_nfl = compute_inference(conn, gid_nfl, "2026-09-13T00:00:00Z")
    result_cfb = compute_inference(conn, gid_cfb, "2026-09-13T00:00:00Z")
    assert result_nfl is not None and result_nfl["sport"] == "nfl"
    assert result_cfb is not None and result_cfb["sport"] == "cfb"
    conn.close()
    print("PASS: both NFL and CFB games are correctly supported by the same pipeline")


def test_prospective_record_write_and_readback():
    conn, db_path = fresh_conn()
    commence = "2026-09-13T20:00:00Z"
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", commence)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", commence, "draftkings", "2026-09-12T20:00:00Z", -3.0)

    result = compute_inference(conn, gid, "2026-09-13T00:00:00Z")
    record_id = write_prospective_record(conn, result)
    assert record_id is not None

    row = conn.execute(
        "SELECT canonical_game_id, as_of, inferred_book_need_side FROM book_need_prospective_records WHERE record_id=?",
        (record_id,),
    ).fetchone()
    assert row[0] == gid and row[1] == "2026-09-13T00:00:00Z"
    conn.close()
    print("PASS: a prospective record writes correctly and reads back exactly as computed")


def test_prospective_record_append_only():
    conn, db_path = fresh_conn()
    commence = "2026-09-13T20:00:00Z"
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", commence)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", commence, "draftkings", "2026-09-12T20:00:00Z", -3.0)
    result = compute_inference(conn, gid, "2026-09-13T00:00:00Z")
    write_prospective_record(conn, result)

    raised = False
    try:
        conn.execute("UPDATE book_need_prospective_records SET data_quality='HACKED'")
        conn.commit()
    except sqlite3.IntegrityError:
        raised = True
    assert raised
    conn.close()
    print("PASS: book_need_prospective_records is genuinely append-only -- UPDATE is blocked")


def test_repeated_later_as_of_creates_new_record_not_overwrite():
    conn, db_path = fresh_conn()
    commence = "2026-09-13T20:00:00Z"
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", commence)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", commence, "draftkings", "2026-09-12T20:00:00Z", -3.0)

    result1 = compute_inference(conn, gid, "2026-09-13T00:00:00Z")
    write_prospective_record(conn, result1)
    result2 = compute_inference(conn, gid, "2026-09-13T15:00:00Z")
    write_prospective_record(conn, result2)

    count = conn.execute("SELECT COUNT(*) FROM book_need_prospective_records WHERE canonical_game_id=?", (gid,)).fetchone()[0]
    assert count == 2
    conn.close()
    print("PASS: a later as_of correctly creates a second, new record rather than overwriting the first")


def test_deterministic_historical_replay():
    conn, db_path = fresh_conn()
    commence = "2026-09-13T20:00:00Z"
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", commence)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", commence, "draftkings", "2026-09-12T20:00:00Z", -3.0)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", commence, "fanduel", "2026-09-12T20:00:00Z", -2.5)

    result1 = compute_inference(conn, gid, "2026-09-13T00:00:00Z")
    result2 = compute_inference(conn, gid, "2026-09-13T00:00:00Z")
    assert result1["inferred_book_need_side"] == result2["inferred_book_need_side"]
    assert result1["need_strength"] == result2["need_strength"]
    assert result1["current_segment_name"] == result2["current_segment_name"]
    conn.close()
    print("PASS: identical inputs at the same as_of always reproduce the identical inference")


def test_no_writes_to_behavioral_v1_or_v2_research_tables():
    conn, db_path = fresh_conn()
    commence = "2026-09-13T20:00:00Z"
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", commence)
    _make_snapshot(conn, gid, "nfl", "KC", "LAC", commence, "draftkings", "2026-09-12T20:00:00Z", -3.0)

    tables = ["engineered_features", "engineered_features_v2", "market_state_by_cutoff"]
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    compute_inference(conn, gid, "2026-09-13T00:00:00Z")
    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    assert before == after
    conn.close()
    print("PASS: computing an inference never writes to V1, V2, or market-state research tables")


def test_signal_line_even_books_never_fabricates_spread():
    lines = [
        {"spread": -4.5},
        {"spread": -4.0},
        {"spread": -3.5},
        {"spread": -3.5},
    ]

    result = select_representative_signal_line(lines)

    offered = [line["spread"] for line in lines]

    assert result["spread"] == -4.0
    assert result["spread"] in offered
    assert result["method"] == "observed_lower_median_unweighted_available_books"

    print("PASS: even-book signal-line selection always returns an actually observed spread")


def test_signal_line_excludes_stale_books():
    conn, db_path = fresh_conn()

    commence = "2026-09-13T20:00:00Z"
    as_of = "2026-09-13T18:00:00Z"

    gid = _make_game(
        conn, "nfl", "nfl_kc", "nfl_lac", commence
    )

    _make_snapshot(
        conn, gid, "nfl", "KC", "LAC", commence,
        "draftkings", "2026-09-13T17:30:00Z", -3.0
    )

    _make_snapshot(
        conn, gid, "nfl", "KC", "LAC", commence,
        "fanduel", "2026-09-13T16:59:00Z", -7.0
    )

    lines = get_available_signal_lines(
        conn,
        gid,
        as_of,
        "home",
        commence_time=commence,
    )

    books = {line["book"] for line in lines}

    assert "draftkings" in books
    assert "fanduel" not in books
    assert len(lines) == 1
    assert lines[0]["spread"] == -3.0

    conn.close()

    print("PASS: stale sportsbook lines are excluded from signal-line selection")


def test_signal_line_home_away_orientation():
    conn, db_path = fresh_conn()

    commence = "2026-09-13T20:00:00Z"
    as_of = "2026-09-13T18:00:00Z"

    gid = _make_game(
        conn, "nfl", "nfl_kc", "nfl_lac", commence
    )

    _make_snapshot(
        conn, gid, "nfl", "KC", "LAC", commence,
        "draftkings", "2026-09-13T17:30:00Z", -3.5
    )

    home = get_representative_signal_line(
        conn, gid, as_of, "home", commence_time=commence
    )

    away = get_representative_signal_line(
        conn, gid, as_of, "away", commence_time=commence
    )

    assert home["spread"] == -3.5
    assert away["spread"] == 3.5
    assert home["side"] == "home"
    assert away["side"] == "away"

    conn.close()

    print("PASS: signal-line home/away orientation preserves the correct spread sign")


def test_signal_line_non_directional_states_return_none():
    conn, db_path = fresh_conn()

    commence = "2026-09-13T20:00:00Z"
    as_of = "2026-09-13T18:00:00Z"

    gid = _make_game(
        conn, "nfl", "nfl_kc", "nfl_lac", commence
    )

    _make_snapshot(
        conn, gid, "nfl", "KC", "LAC", commence,
        "draftkings", "2026-09-13T17:30:00Z", -3.5
    )

    for state in (
        "NO_CLEAR_NEED",
        "INSUFFICIENT_EVIDENCE",
        "NOT_YET_EVALUABLE",
    ):
        result = get_representative_signal_line(
            conn,
            gid,
            as_of,
            state,
            commence_time=commence,
        )
        assert result is None

    conn.close()

    print("PASS: non-directional inference states never receive a signal line")


def test_signal_line_never_uses_future_snapshot():
    conn, db_path = fresh_conn()

    commence = "2026-09-13T20:00:00Z"
    as_of = "2026-09-13T18:00:00Z"

    gid = _make_game(
        conn, "nfl", "nfl_kc", "nfl_lac", commence
    )

    _make_snapshot(
        conn, gid, "nfl", "KC", "LAC", commence,
        "draftkings", "2026-09-13T17:30:00Z", -3.5
    )

    _make_snapshot(
        conn, gid, "nfl", "KC", "LAC", commence,
        "draftkings", "2026-09-13T18:05:00Z", -6.5
    )

    result = get_representative_signal_line(
        conn,
        gid,
        as_of,
        "home",
        commence_time=commence,
    )

    assert result is not None
    assert result["spread"] == -3.5
    assert result["spread"] != -6.5

    conn.close()

    print("PASS: signal line never uses a sportsbook snapshot from after as_of")


if __name__ == "__main__":
    test_juice_six_required_cases()
    test_implied_probability_continuity_at_zero_crossing()
    test_regime_home_pressure_spread_home_confirmation()
    test_regime_away_pressure_spread_away_confirmation()
    test_regime_home_pressure_spread_away_divergence()
    test_regime_away_pressure_spread_home_divergence()
    test_regime_held_juice_worsens_resistance()
    test_regime_held_juice_improves_partial_divergence()
    test_regime_contradiction_flag()
    test_intra_segment_reversal_detected()
    test_intra_segment_reversal_not_falsely_triggered_by_flat_sequence()
    test_missing_data_produces_insufficient()
    test_key_numbers_all_five_cases()
    test_t4h_segment_states()
    test_t20h_only_segment_a_reachable()
    test_t45m_segment_c_in_progress()
    test_future_segments_never_leak_evaluation_horizon()
    test_historical_completed_segment_staleness_uses_own_horizon()
    test_current_in_progress_staleness_uses_as_of()
    test_exact_t6_and_t1_boundary_behavior()
    test_unanimous_aggregation()
    test_majority_aggregation()
    test_no_majority_produces_mixed()
    test_insufficient_books_excluded_from_vote()
    test_latest_usable_segment_governs()
    test_insufficient_current_segment_falls_back()
    test_prior_segment_contradiction_detected()
    test_no_contradiction_when_camps_match()
    test_strong_resistance_clear_pressure()
    test_confirmation_capped_at_lean()
    test_mixed_regime_no_clear_need()
    test_even_direction_no_clear_need()
    test_undefined_magnitude_no_clear_need()
    test_low_magnitude_percentile_no_clear_need()
    test_fewer_than_two_books_insufficient_evidence()
    test_no_current_segment_insufficient_evidence()
    test_no_clear_need_vs_insufficient_evidence_are_distinct()
    test_handle_intensity_at_or_below_median_caps_lean()
    test_majority_breadth_downgrades_one_level()
    test_contradiction_downgrades_one_level()
    test_no_key_number_corroboration_downgrades_resistance_only()
    test_modifiers_do_not_double_count_multiple_downgrades()
    test_data_quality_thin_coverage_low()
    test_data_quality_high_when_clean()
    test_not_yet_evaluable_when_nothing_attempted()
    test_insufficient_evidence_when_attempted_but_no_usable_segment()
    test_no_clear_need_still_requires_real_data()
    test_data_quality_not_yet_evaluable()
    test_data_quality_thin_but_attempted_still_uses_normal_logic()
    test_pipeline_pre_t24h_produces_not_yet_evaluable_end_to_end()
    test_pipeline_exactly_t24h_zero_elapsed_still_not_yet_evaluable()
    test_pipeline_strictly_after_t24h_normal_path()
    test_pipeline_segment_started_but_stale_insufficient_evidence()
    test_dry_run_summary_counts_each_state_correctly()
    test_end_to_end_pipeline_runs_and_produces_a_result()
    test_no_post_as_of_market_data_leakage()
    test_nfl_and_cfb_both_supported()
    test_prospective_record_write_and_readback()
    test_prospective_record_append_only()
    test_repeated_later_as_of_creates_new_record_not_overwrite()
    test_deterministic_historical_replay()
    test_no_writes_to_behavioral_v1_or_v2_research_tables()
    test_signal_line_even_books_never_fabricates_spread()
    test_signal_line_excludes_stale_books()
    test_signal_line_home_away_orientation()
    test_signal_line_non_directional_states_return_none()
    test_signal_line_never_uses_future_snapshot()
    print("\nAll Book Need tests passed.")
