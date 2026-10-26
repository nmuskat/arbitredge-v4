"""
Tests for validation/harness/splitting.py, fdr.py, statistics.py, metrics.py

Run with: python tests/test_validation_harness_math.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.harness.splitting import generate_walk_forward_folds  # noqa: E402
from validation.harness.fdr import benjamini_hochberg  # noqa: E402
from validation.harness.statistics import evaluate_paired_comparison, MIN_FOLDS_FOR_ANY_TEST  # noqa: E402
from validation.harness.metrics import signed_improvement, HIGHER_IS_BETTER  # noqa: E402


def test_folds_are_chronologically_correct():
    from datetime import datetime, timedelta
    base = datetime(2025, 9, 1, 20, 0, 0)
    games = [(i, (base + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")) for i in range(1, 51)]
    import random
    random.seed(1)
    shuffled = games.copy()
    random.shuffle(shuffled)

    folds = generate_walk_forward_folds(shuffled, n_folds=5, min_train_size=10)
    assert len(folds) > 0
    for f in folds:
        assert f["train_max_commence_time"] < f["validation_min_commence_time"]
    print("PASS: every generated fold is chronologically correct regardless of input order")


def test_tied_commence_times_never_split_across_a_boundary():
    """The exact bug caught during testing: games sharing an identical
    commence_time (routine in reality -- simultaneous kickoffs) must never
    end up split between train and validation at the same boundary."""
    games = []
    gid = 1
    for day in range(1, 15):
        ts = f"2025-09-{day:02d}T13:00:00Z"
        for _ in range(4):  # 4 games tied at the same kickoff time, like a real Sunday early window
            games.append((gid, ts))
            gid += 1

    folds = generate_walk_forward_folds(games, n_folds=4, min_train_size=20)
    assert len(folds) > 0
    for f in folds:
        assert f["train_max_commence_time"] < f["validation_min_commence_time"]
    train_val_id_sets = [(set(f["train_ids"]), set(f["validation_ids"])) for f in folds]
    for train_ids, val_ids in train_val_id_sets:
        assert train_ids.isdisjoint(val_ids)
    print("PASS: games sharing an identical commence_time are never split across a fold boundary "
          "(this reproduces a real bug caught during testing, now fixed)")


def _make_unique_time_games(n=50):
    from datetime import datetime, timedelta
    base = datetime(2025, 9, 1, 20, 0, 0)
    return [(i, (base + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")) for i in range(1, n + 1)]


def test_expanding_window_grows():
    games = _make_unique_time_games(50)
    folds = generate_walk_forward_folds(games, n_folds=4, min_train_size=10)
    train_sizes = [len(f["train_ids"]) for f in folds]
    assert train_sizes == sorted(train_sizes)
    assert train_sizes[-1] > train_sizes[0]
    print("PASS: walk-forward folds use an expanding training window")


def test_insufficient_games_returns_empty_list():
    games = [(1, "2025-09-01T20:00:00Z"), (2, "2025-09-02T20:00:00Z")]
    folds = generate_walk_forward_folds(games, n_folds=5, min_train_size=10)
    assert folds == []
    print("PASS: too few games returns an empty fold list, not an error or a fabricated fold")


def test_no_game_appears_in_both_train_and_validation_same_fold():
    games = _make_unique_time_games(50)
    folds = generate_walk_forward_folds(games, n_folds=5, min_train_size=10)
    for f in folds:
        assert set(f["train_ids"]).isdisjoint(set(f["validation_ids"]))
    print("PASS: no game ever appears in both train and validation within the same fold")


def test_bh_all_significant_when_all_tiny():
    p_values = [0.001, 0.002, 0.003]
    result = benjamini_hochberg(p_values, alpha=0.05)
    assert all(sig == 1 for _, sig in result)
    print("PASS: uniformly tiny p-values are all significant after BH correction")


def test_bh_none_significant_when_all_large():
    p_values = [0.9, 0.8, 0.7]
    result = benjamini_hochberg(p_values, alpha=0.05)
    assert all(sig == 0 for _, sig in result)
    print("PASS: uniformly large p-values are all non-significant after BH correction")


def test_bh_more_lenient_than_bonferroni():
    p_values = [0.01, 0.02, 0.03, 0.5, 0.9]
    result = benjamini_hochberg(p_values, alpha=0.05)
    n_sig_bh = sum(1 for _, s in result if s == 1)
    n_sig_bonferroni = sum(1 for p in p_values if p * len(p_values) <= 0.05)
    assert n_sig_bh >= n_sig_bonferroni
    print("PASS: BH correction is appropriately more lenient than a strict Bonferroni bound")


def test_bh_handles_none_entries():
    p_values = [0.01, None, 0.5]
    result = benjamini_hochberg(p_values)
    assert result[1] == (None, None)
    print("PASS: BH correction handles None entries without crashing or fabricating a result")


def test_zero_diffs_produce_high_p_value():
    diffs = [0.0, 0.0, 0.0, 0.0, 0.0]
    result = evaluate_paired_comparison(diffs)
    assert result["raw_p_value"] > 0.5
    print("PASS: zero fold differences produce a high (non-significant) p-value")


def test_consistent_large_diffs_produce_low_p_value():
    diffs = [0.1, 0.12, 0.09, 0.11, 0.1, 0.13]
    result = evaluate_paired_comparison(diffs)
    assert result["raw_p_value"] < 0.05
    assert result["ci_lower"] > 0
    print("PASS: consistently large, same-direction fold differences produce a low p-value and a CI excluding 0")


def test_insufficient_folds_flagged():
    diffs = [0.1, 0.2]
    result = evaluate_paired_comparison(diffs)
    assert result["insufficient_sample"] is True
    assert result["statistical_test_used"] == "insufficient_sample"
    print(f"PASS: fewer than {MIN_FOLDS_FOR_ANY_TEST} folds is correctly flagged insufficient_sample")


def test_dependence_warning_always_set():
    diffs = [0.1, 0.2, 0.3, 0.1, 0.2, 0.15, 0.25, 0.18, 0.22]
    result = evaluate_paired_comparison(diffs)
    assert result["dependence_warning"] is True
    print("PASS: dependence_warning is set even for a comparison with enough folds to run crosschecks")


def test_exact_permutation_used_for_small_n():
    diffs = [0.1, 0.2, 0.15]
    result = evaluate_paired_comparison(diffs)
    assert result["statistical_test_used"] == "permutation_exact"
    print("PASS: small fold counts use exact permutation enumeration, not a Monte Carlo approximation")


import math


def test_signed_improvement_direction_for_auc():
    assert HIGHER_IS_BETTER["auc"] is True
    assert math.isclose(signed_improvement(0.60, 0.65, "auc"), 0.05)
    assert math.isclose(signed_improvement(0.65, 0.60, "auc"), -0.05)


def test_signed_improvement_direction_for_mae():
    assert HIGHER_IS_BETTER["mae"] is False
    assert math.isclose(signed_improvement(5.0, 3.0, "mae"), 2.0)
    assert math.isclose(signed_improvement(3.0, 5.0, "mae"), -2.0)
    print("PASS: signed_improvement correctly orients both higher-is-better (AUC) and "
          "lower-is-better (MAE) metrics so positive always means 'candidate improved'")


def test_signed_improvement_none_propagates():
    assert signed_improvement(None, 0.5, "auc") is None
    assert signed_improvement(0.5, None, "auc") is None
    print("PASS: signed_improvement propagates None rather than fabricating a value")


if __name__ == "__main__":
    test_folds_are_chronologically_correct()
    test_tied_commence_times_never_split_across_a_boundary()
    test_expanding_window_grows()
    test_insufficient_games_returns_empty_list()
    test_no_game_appears_in_both_train_and_validation_same_fold()
    test_bh_all_significant_when_all_tiny()
    test_bh_none_significant_when_all_large()
    test_bh_more_lenient_than_bonferroni()
    test_bh_handles_none_entries()
    test_zero_diffs_produce_high_p_value()
    test_consistent_large_diffs_produce_low_p_value()
    test_insufficient_folds_flagged()
    test_dependence_warning_always_set()
    test_exact_permutation_used_for_small_n()
    test_signed_improvement_direction_for_auc()
    test_signed_improvement_direction_for_mae()
    test_signed_improvement_none_propagates()
    print("\nAll validation harness math tests passed.")
