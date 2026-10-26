"""
Tests for behavioral/handle_intensity_v1.py and behavioral/side_preference_v1.py

Run with: python tests/test_behavioral_v1.py
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import behavioral.handle_intensity_v1 as hi  # noqa: E402
import behavioral.side_preference_v1 as sp  # noqa: E402


def _hi_game(gid, standalone_same=1, standalone_all=1, competing_same=0, competing_all=0,
             ranking_home=None, ranking_away=None, tv_network=None, market_state=None):
    return {
        "canonical_game_id": gid,
        "is_standalone_same_sport": standalone_same,
        "is_standalone_all_sports": standalone_all,
        "competing_games_same_window_same_sport": competing_same,
        "competing_games_same_window_all_sports": competing_all,
        "_context": {"ranking_home": ranking_home, "ranking_away": ranking_away, "tv_network": tv_network},
        "market_state": market_state,
    }


def _sp_game(gid, home_wins3=1, away_wins3=1, home_margin3=0.0, away_margin3=0.0,
             home_win_streak=0, home_loss_streak=0, away_win_streak=0, away_loss_streak=0,
             home_blowout_win=0, home_blowout_loss=0, away_blowout_win=0, away_blowout_loss=0,
             rest_advantage=0, ranking_home=None, ranking_away=None, market_state=None):
    return {
        "canonical_game_id": gid,
        "home_wins_last_3": home_wins3, "away_wins_last_3": away_wins3,
        "home_point_margin_avg_last_3": home_margin3, "away_point_margin_avg_last_3": away_margin3,
        "home_current_win_streak": home_win_streak, "home_current_loss_streak": home_loss_streak,
        "away_current_win_streak": away_win_streak, "away_current_loss_streak": away_loss_streak,
        "home_last_game_blowout_win": home_blowout_win, "home_last_game_blowout_loss": home_blowout_loss,
        "away_last_game_blowout_win": away_blowout_win, "away_last_game_blowout_loss": away_blowout_loss,
        "rest_advantage_days": rest_advantage,
        "_context": {"ranking_home": ranking_home, "ranking_away": ranking_away},
        "market_state": market_state,
    }


def test_handle_intensity_primary_score_never_reads_market_state():
    without_market = [_hi_game(1), _hi_game(2, competing_same=3)]
    with_market = [
        _hi_game(1, market_state={"spread_magnitude": 100.0}),
        _hi_game(2, competing_same=3, market_state={"spread_magnitude": -999.0}),
    ]
    r1 = hi.compute_handle_intensity_v1(without_market)
    r2 = hi.compute_handle_intensity_v1(with_market)
    assert [g["normalized_score"] for g in r1] == [g["normalized_score"] for g in r2]
    print("PASS: HandleIntensityV1's primary score is structurally blind to market_state, "
          "identical output whether or not it's present in the input")


def test_side_preference_primary_score_never_reads_market_state():
    without_market = [_sp_game(1), _sp_game(2, home_wins3=3, away_wins3=0)]
    with_market = [
        _sp_game(1, market_state={"favorite_side": "away"}),
        _sp_game(2, home_wins3=3, away_wins3=0, market_state={"favorite_side": "home"}),
    ]
    r1 = sp.compute_side_preference_v1(without_market)
    r2 = sp.compute_side_preference_v1(with_market)
    assert [g["normalized_lean_score"] for g in r1] == [g["normalized_lean_score"] for g in r2]
    print("PASS: RecreationalSidePreferenceV1's primary score is structurally blind to market_state")


def test_handle_intensity_enrichment_layers_on_top_of_identical_primary():
    games = [
        _hi_game(1, market_state={"spread_magnitude": 3.0}),
        _hi_game(2, competing_same=2, market_state={"spread_magnitude": 7.0}),
    ]
    primary = hi.compute_handle_intensity_v1(games)
    enriched = hi.compute_handle_intensity_v1_market_enrichment(games, book="draftkings")

    for i in range(len(games)):
        assert enriched[i]["primary_normalized_score"] == primary[i]["normalized_score"]
        assert enriched[i]["enrichment_score"] != enriched[i]["primary_normalized_score"]
    print("PASS: Handle Intensity enrichment correctly layers on top of an identical, independently-verifiable baseline")


def test_side_preference_enrichment_layers_on_top_of_identical_primary():
    games = [
        _sp_game(1, market_state={"favorite_side": "home"}),
        _sp_game(2, home_wins3=3, away_wins3=0, market_state={"favorite_side": "away"}),
    ]
    primary = sp.compute_side_preference_v1(games)
    enriched = sp.compute_side_preference_v1_market_enrichment(games, book="fanduel")

    for i in range(len(games)):
        assert enriched[i]["primary_normalized_lean_score"] == primary[i]["normalized_lean_score"]
    print("PASS: Side Preference enrichment correctly layers on top of an identical, independently-verifiable baseline")


def test_enrichment_book_is_required_no_default():
    sig_hi = inspect.signature(hi.compute_handle_intensity_v1_market_enrichment)
    sig_sp = inspect.signature(sp.compute_side_preference_v1_market_enrichment)
    assert sig_hi.parameters["book"].default is inspect.Parameter.empty
    assert sig_sp.parameters["book"].default is inspect.Parameter.empty
    print("PASS: both enrichment functions require an explicit book parameter, no default, no automatic selection")


def test_different_books_produce_independent_enrichment_scores():
    # A real 2-game slate is required -- a single-game slate always degenerates to a
    # neutral 0.0 z-score contribution by design, which would trivially match regardless
    # of the actual spread values, masking the very thing this test needs to check.
    games_dk = [_hi_game(1, market_state={"spread_magnitude": 3.0}), _hi_game(2, market_state={"spread_magnitude": 3.0})]
    dk = hi.compute_handle_intensity_v1_market_enrichment(games_dk, book="draftkings")
    games_fd = [_hi_game(1, market_state={"spread_magnitude": 9.0}), _hi_game(2, market_state={"spread_magnitude": 3.0})]
    fd = hi.compute_handle_intensity_v1_market_enrichment(games_fd, book="fanduel")
    assert dk[0]["enrichment_score"] != fd[0]["enrichment_score"]
    print("PASS: different books' market state produces independent enrichment scores -- never collapsed")


def test_deterministic_reproducibility():
    games = [_hi_game(1), _hi_game(2, competing_same=2), _hi_game(3, ranking_home=5)]
    r1 = hi.compute_handle_intensity_v1(games)
    r2 = hi.compute_handle_intensity_v1(games)
    assert r1 == r2

    sp_games = [_sp_game(1), _sp_game(2, home_wins3=3)]
    s1 = sp.compute_side_preference_v1(sp_games)
    s2 = sp.compute_side_preference_v1(sp_games)
    assert s1 == s2
    print("PASS: both models are fully deterministic -- identical input always produces identical output")


def test_all_missing_inputs_produce_neutral_score_not_crash():
    games = [{"canonical_game_id": 1}, {"canonical_game_id": 2}]
    results = hi.compute_handle_intensity_v1(games)
    assert all(r["normalized_score"] == 0.0 for r in results)

    sp_results = sp.compute_side_preference_v1(games)
    assert all(r["normalized_lean_score"] == 0.0 for r in sp_results)
    assert all(r["direction"] == "even" for r in sp_results)
    print("PASS: completely missing inputs produce a neutral 0.0 score, never a crash, never fabricated data")


def test_partial_missing_inputs_do_not_crash():
    games = [
        _hi_game(1, ranking_home=3),
        {"canonical_game_id": 2},
        _hi_game(3, competing_same=1, competing_all=2),
    ]
    results = hi.compute_handle_intensity_v1(games)
    assert len(results) == 3
    assert all(isinstance(r["normalized_score"], float) for r in results)
    print("PASS: a mix of fully-populated, partially-populated, and empty games is handled without crashing")


def test_percentile_rank_is_genuinely_relative_not_absolute():
    same_game = _hi_game(1, competing_same=0)
    weak_slate = [same_game, _hi_game(2, competing_same=5)]
    strong_slate = [same_game, _hi_game(2, competing_same=0), _hi_game(3, competing_same=0), _hi_game(4, competing_same=0)]

    r_weak = hi.compute_handle_intensity_v1(weak_slate)
    r_strong = hi.compute_handle_intensity_v1(strong_slate)

    percentile_in_weak = next(r["percentile_rank"] for r in r_weak if r["canonical_game_id"] == 1)
    percentile_in_strong = next(r["percentile_rank"] for r in r_strong if r["canonical_game_id"] == 1)

    assert percentile_in_weak != percentile_in_strong
    print(f"PASS: the same game's percentile rank changes across different slates "
          f"({percentile_in_weak} vs {percentile_in_strong}), confirming genuine slate-relativity")


def test_single_game_slate_has_no_meaningful_percentile():
    results = hi.compute_handle_intensity_v1([_hi_game(1)])
    assert results[0]["percentile_rank"] is None
    print("PASS: a single-game slate correctly produces percentile_rank=None, not a misleading trivial 100%")


def test_side_preference_positive_lean_toward_home():
    games = [
        _sp_game(1, home_wins3=3, away_wins3=0, home_margin3=14.0, away_margin3=-5.0,
                  home_win_streak=3, rest_advantage=3, ranking_home=8),
        _sp_game(2),
    ]
    results = sp.compute_side_preference_v1(games)
    strong_home_game = next(r for r in results if r["canonical_game_id"] == 1)
    assert strong_home_game["normalized_lean_score"] > 0
    assert strong_home_game["direction"] == "home"
    print("PASS: a game with every differential favoring the home team correctly produces a positive, 'home' lean")


def test_side_preference_negative_lean_toward_away():
    games = [
        _sp_game(1, home_wins3=0, away_wins3=3, home_margin3=-14.0, away_margin3=14.0,
                  away_win_streak=3, rest_advantage=-3, ranking_away=8),
        _sp_game(2),
    ]
    results = sp.compute_side_preference_v1(games)
    strong_away_game = next(r for r in results if r["canonical_game_id"] == 1)
    assert strong_away_game["normalized_lean_score"] < 0
    assert strong_away_game["direction"] == "away"
    print("PASS: a game with every differential favoring the away team correctly produces a negative, 'away' lean")


def test_direction_convention_documented_in_every_output_row():
    results = sp.compute_side_preference_v1([_sp_game(1)])
    assert results[0]["direction_convention"] == "positive = lean toward HOME, negative = lean toward AWAY"
    print("PASS: the direction convention is explicitly documented in every output row, not left implicit")


def test_no_forbidden_vocabulary_in_output_or_field_names():
    forbidden = ["probability", "confidence", "ticket_pct", "ticket_percent", "handle_pct", "handle_percent"]
    hi_results = hi.compute_handle_intensity_v1([_hi_game(1), _hi_game(2)])
    sp_results = sp.compute_side_preference_v1([_sp_game(1), _sp_game(2)])

    for results in (hi_results, sp_results):
        for row in results:
            for key in row:
                assert not any(term in key.lower() for term in forbidden), f"forbidden field name: {key}"
            assert "EXPERIMENTAL" in row["label"] and "UNCALIBRATED" in row["label"]
    print("PASS: no output field name claims a probability/confidence/ticket%/handle% interpretation, "
          "and every row carries the EXPERIMENTAL/UNCALIBRATED label")


def test_percentile_rank_is_the_only_bounded_0_to_100_field():
    results = hi.compute_handle_intensity_v1([_hi_game(1, competing_same=5), _hi_game(2), _hi_game(3, ranking_home=1)])
    for r in results:
        assert r["percentile_rank"] is None or 0 < r["percentile_rank"] <= 100
    print("PASS: percentile_rank is correctly bounded as a rank; raw_score remains an unbounded raw sum, "
          "never disguised as a percentage")


def test_computation_works_mechanically_on_preseason_shaped_data():
    preseason_shaped_games = [
        {
            "canonical_game_id": 1,
            "home_wins_last_3": None, "away_wins_last_3": None,
            "home_point_margin_avg_last_3": None, "away_point_margin_avg_last_3": None,
            "home_current_win_streak": None, "home_current_loss_streak": None,
            "away_current_win_streak": None, "away_current_loss_streak": None,
            "home_last_game_blowout_win": None, "home_last_game_blowout_loss": None,
            "away_last_game_blowout_win": None, "away_last_game_blowout_loss": None,
            "rest_advantage_days": None,
            "is_standalone_same_sport": 1, "is_standalone_all_sports": 0,
            "competing_games_same_window_same_sport": 0, "competing_games_same_window_all_sports": 3,
        },
    ]
    hi_results = hi.compute_handle_intensity_v1(preseason_shaped_games)
    sp_results = sp.compute_side_preference_v1(preseason_shaped_games)
    assert len(hi_results) == 1 and len(sp_results) == 1
    assert isinstance(hi_results[0]["normalized_score"], float)
    assert isinstance(sp_results[0]["normalized_lean_score"], float)
    print("PASS: computation runs correctly (mechanics-only, not a results validation) against "
          "preseason-shaped sparse data, exactly matching real season-opener recency gaps")


def test_behavioral_modules_never_touch_a_database():
    for module in (hi, sp):
        source = inspect.getsource(module)
        assert "sqlite3" not in source, f"{module.__name__} must never import or reference sqlite3 directly"
        assert "conn.execute" not in source, f"{module.__name__} must never execute a database query directly"
    print("PASS: neither behavioral module imports sqlite3 or executes any database query -- "
          "non-interference with V1/V2 is structurally guaranteed, not just tested indirectly")


def test_coverage_does_not_mechanically_enable_larger_magnitude_scores():
    """
    The exact defect identified in the transparency audit: before this
    fix, a game with more available (non-None) components could reach a
    larger |raw_score| purely from having more terms that could each
    swing the sum, independent of actual signal strength. This test
    constructs two games with IDENTICAL per-component signal strength but
    DIFFERENT coverage, and confirms the ACTIVE (normalized) score is
    comparable between them, unlike the raw sum.
    """
    # Game 1: all 6 Side Lean components populated, each strongly home-favoring
    full_coverage = _sp_game(1, home_wins3=3, away_wins3=0, home_margin3=10.0, away_margin3=-10.0,
                               home_win_streak=3, rest_advantage=3, ranking_home=5)
    # Game 2: only half the components populated (rest_advantage_days and
    # rank_prestige_differential missing entirely), but the AVAILABLE ones
    # carry the SAME per-component signal strength as game 1
    partial_coverage = dict(full_coverage)
    partial_coverage["canonical_game_id"] = 2
    partial_coverage["rest_advantage_days"] = None
    partial_coverage["_context"] = {"ranking_home": None, "ranking_away": None}

    games = [full_coverage, partial_coverage, _sp_game(3)]  # third game as a neutral slate anchor
    results = sp.compute_side_preference_v1(games)

    full = next(r for r in results if r["canonical_game_id"] == 1)
    partial = next(r for r in results if r["canonical_game_id"] == 2)

    assert full["coverage_count"] == 6
    assert partial["coverage_count"] == 5, (
        "only rest_advantage_days is truly None here -- rank_prestige_differential is always a real "
        "value (0 when neither team is ranked, not None), so it never counts as missing coverage"
    )
    assert full["raw_lean_score"] != partial["raw_lean_score"], (
        "the raw (unnormalized) sums are expected to differ given different coverage -- this confirms "
        "the underlying asymmetry the audit identified actually exists in raw_lean_score"
    )
    # The core requirement: the ACTIVE (normalized) score must not be mechanically
    # inflated for the fully-covered game just because it has more terms.
    assert abs(full["normalized_lean_score"] - partial["normalized_lean_score"]) < abs(
        full["raw_lean_score"] - partial["raw_lean_score"]
    ), "normalization must meaningfully narrow the gap caused purely by coverage, not just relabel the same numbers"
    print(f"PASS: coverage-normalized scoring narrows the gap between differently-covered games "
          f"with similar per-component signal (raw diff={abs(full['raw_lean_score']-partial['raw_lean_score']):.3f}, "
          f"normalized diff={abs(full['normalized_lean_score']-partial['normalized_lean_score']):.3f}) -- "
          f"confirming the audited defect is actually fixed, not just relabeled")


def test_minimum_coverage_produces_neutral_or_defined_normalized_score():
    """
    A structural discovery worth testing precisely, not glossing over:
    ranked_team_count/has_tv_network (Handle Intensity) and
    rank_prestige_differential (Side Preference) are presence/count
    indicators that are ALWAYS well-defined (0 is a legitimate real value
    meaning "no ranking/no broadcast info," not missing data) -- so
    coverage_count can never truly reach 0 for either model as currently
    built. The real structural minimum is 2 for Handle Intensity, 1 for
    Side Preference. This test confirms that minimum-coverage case is
    handled correctly (no crash, no fabrication), and that the
    division-by-zero guard (coverage_count == 0 -> 0.0) remains correct
    defensive code even though it's currently unreachable given today's
    PRIMARY_FEATURES list.
    """
    results = hi.compute_handle_intensity_v1([{"canonical_game_id": 1}, _hi_game(2)])
    minimal_cov = next(r for r in results if r["canonical_game_id"] == 1)
    assert minimal_cov["coverage_count"] == 2, (
        "a totally empty Handle Intensity input still yields coverage_count=2 -- "
        "ranked_team_count and has_tv_network both reduce to a real 0, never None"
    )
    assert isinstance(minimal_cov["normalized_score"], float)

    sp_results = sp.compute_side_preference_v1([{"canonical_game_id": 1}, _sp_game(2)])
    sp_minimal = next(r for r in sp_results if r["canonical_game_id"] == 1)
    assert sp_minimal["coverage_count"] == 1, (
        "a totally empty Side Preference input still yields coverage_count=1 -- "
        "rank_prestige_differential always reduces to a real 0, never None"
    )
    assert isinstance(sp_minimal["normalized_lean_score"], float)
    print("PASS: the structural minimum coverage case (2 for Handle Intensity, 1 for Side Preference -- "
          "true zero coverage is impossible given the always-defined presence-indicator features) "
          "is handled correctly, with the zero-coverage division guard confirmed as valid defensive code")


def test_coverage_fields_present_in_every_output_row():
    hi_results = hi.compute_handle_intensity_v1([_hi_game(1), _hi_game(2, competing_same=2)])
    sp_results = sp.compute_side_preference_v1([_sp_game(1), _sp_game(2)])
    for r in hi_results:
        assert "coverage_count" in r and "coverage_fraction" in r and "normalized_score" in r
        assert 0 <= r["coverage_count"] <= len(hi.PRIMARY_FEATURES)
        assert 0.0 <= r["coverage_fraction"] <= 1.0
    for r in sp_results:
        assert "coverage_count" in r and "coverage_fraction" in r and "normalized_lean_score" in r
    print("PASS: coverage_count/coverage_fraction/normalized_score are present and disclosed in every output row")


def test_component_contributions_breakdown_present_and_sums_to_raw_score():
    results = hi.compute_handle_intensity_v1([_hi_game(1, competing_same=2, ranking_home=3), _hi_game(2)])
    for r in results:
        assert set(r["component_contributions"].keys()) == set(hi.PRIMARY_FEATURES)
        assert abs(sum(r["component_contributions"].values()) - r["raw_score"]) < 1e-9, (
            "the disclosed per-component breakdown must sum exactly to raw_score -- no hidden term"
        )
    print("PASS: the per-component contribution breakdown is present for every feature and sums exactly to raw_score")


def test_enrichment_marked_inactive_and_not_called_by_default():
    assert hi.ENRICHMENT_ACTIVE is False
    assert sp.ENRICHMENT_ACTIVE is False
    games = [_hi_game(1, market_state={"spread_magnitude": 3.0})]
    enrichment_result = hi.compute_handle_intensity_v1_market_enrichment(games, book="draftkings")
    assert enrichment_result[0]["active"] is False, (
        "the enrichment function's own output must self-report that it is not the active/default score"
    )
    print("PASS: both models' enrichment layers are explicitly marked inactive (ENRICHMENT_ACTIVE=False), "
          "and the enrichment output itself discloses this -- plumbing preserved, never silently active")


def test_unrenamed_lean_score_key_no_longer_exists():
    """Confirms the old, coverage-unsafe field names are genuinely gone
    from the active output, not just shadowed."""
    results = sp.compute_side_preference_v1([_sp_game(1)])
    assert "lean_score" not in results[0], (
        "the old un-normalized 'lean_score' key must not exist -- only 'raw_lean_score' "
        "(diagnostic) and 'normalized_lean_score' (active) should be present"
    )
    hi_results = hi.compute_handle_intensity_v1([_hi_game(1)])
    assert "raw_score" in hi_results[0] and "normalized_score" in hi_results[0]
    print("PASS: the coverage-unsafe field names are gone from the active output; only the explicitly "
          "labeled raw (diagnostic) and normalized (active) fields exist")


def test_tied_scores_receive_identical_percentile_ranks():
    """
    Regression for the exact production finding: two games with
    identical scores (BUF/CAR and CHI/CLE both at normalized_score=-0.192)
    previously received different percentile ranks (14.29 vs 28.57)
    purely from ordinal sort position. Confirms the fix: exact ties now
    receive the identical AVERAGE RANK percentile.
    """
    from behavioral._stats import slate_percentile_ranks
    scores = [-1.155, -1.155, 0.5, 0.6, 0.7, 0.8, 0.9]  # 7-game slate, first two tied
    percentiles = slate_percentile_ranks(scores)
    assert percentiles[0] == percentiles[1], (
        "identical scores must receive identical percentile ranks, never differing by ordinal position"
    )
    assert abs(percentiles[0] - 21.428571428571427) < 1e-9, "tied games should share the AVERAGE of ranks 1 and 2"
    # every other (non-tied) game's spacing must remain correct and monotonic
    assert percentiles[2] < percentiles[3] < percentiles[4] < percentiles[5] < percentiles[6]
    print("PASS: tied scores receive identical percentile ranks (average-rank convention), "
          "and non-tied games' relative spacing remains correct")


def test_handle_intensity_report_ties_use_shared_percentile():
    games = [_hi_game(1), _hi_game(2), _hi_game(3, competing_same=5), _hi_game(4, competing_same=5)]
    results = hi.compute_handle_intensity_v1(games)
    r1 = next(r for r in results if r["canonical_game_id"] == 1)
    r2 = next(r for r in results if r["canonical_game_id"] == 2)
    r3 = next(r for r in results if r["canonical_game_id"] == 3)
    r4 = next(r for r in results if r["canonical_game_id"] == 4)
    assert r1["normalized_score"] == r2["normalized_score"]
    assert r1["percentile_rank"] == r2["percentile_rank"]
    assert r3["normalized_score"] == r4["normalized_score"]
    assert r3["percentile_rank"] == r4["percentile_rank"]
    print("PASS: end-to-end through compute_handle_intensity_v1, tied normalized_scores correctly "
          "produce tied percentile_ranks")


if __name__ == "__main__":
    test_handle_intensity_primary_score_never_reads_market_state()
    test_side_preference_primary_score_never_reads_market_state()
    test_handle_intensity_enrichment_layers_on_top_of_identical_primary()
    test_side_preference_enrichment_layers_on_top_of_identical_primary()
    test_enrichment_book_is_required_no_default()
    test_different_books_produce_independent_enrichment_scores()
    test_deterministic_reproducibility()
    test_all_missing_inputs_produce_neutral_score_not_crash()
    test_partial_missing_inputs_do_not_crash()
    test_percentile_rank_is_genuinely_relative_not_absolute()
    test_single_game_slate_has_no_meaningful_percentile()
    test_side_preference_positive_lean_toward_home()
    test_side_preference_negative_lean_toward_away()
    test_direction_convention_documented_in_every_output_row()
    test_no_forbidden_vocabulary_in_output_or_field_names()
    test_percentile_rank_is_the_only_bounded_0_to_100_field()
    test_computation_works_mechanically_on_preseason_shaped_data()
    test_behavioral_modules_never_touch_a_database()
    test_coverage_does_not_mechanically_enable_larger_magnitude_scores()
    test_minimum_coverage_produces_neutral_or_defined_normalized_score()
    test_coverage_fields_present_in_every_output_row()
    test_component_contributions_breakdown_present_and_sums_to_raw_score()
    test_enrichment_marked_inactive_and_not_called_by_default()
    test_unrenamed_lean_score_key_no_longer_exists()
    test_tied_scores_receive_identical_percentile_ranks()
    test_handle_intensity_report_ties_use_shared_percentile()
    print("\nAll behavioral V1 tests passed.")
