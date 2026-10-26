"""
validation/harness/statistics.py

Paired-comparison significance testing for marginal improvement across
walk-forward folds. Design, per explicit instruction not to default to a
casual paired t-test:

  - PRIMARY test: a paired sign-flip permutation test. Exact enumeration
    (2^n sign patterns) when n <= MAX_EXACT_FOLDS, Monte Carlo sampling
    otherwise. Chosen as primary because it makes no distributional
    assumption at all, which matters given how few folds this project can
    realistically produce -- the most broadly defensible default across
    the whole range of sample sizes this harness will actually see.
  - Bootstrap confidence interval: always computed (also assumption-free),
    reported alongside the p-value.
  - Wilcoxon signed-rank and paired t-test: computed and reported ONLY as
    supplementary cross-checks (stored in `notes`, not as the primary
    stored p-value), and ONLY when their own assumptions are plausible
    (Wilcoxon: n large enough for its asymptotic approximation to be
    reasonable; t-test: only when a normality check on the fold-level
    differences doesn't clearly fail). They never replace the permutation
    test as the number the FDR procedure operates on -- mixing test types
    within one FDR family would make the correction incoherent.
  - dependence_warning is set on every result. Walk-forward folds share
    overlapping expanding training windows and the same underlying season
    dynamics -- they are not obviously independent observations, and this
    harness does not pretend otherwise even when a test nominally assumes
    independence.
"""

import itertools
import numpy as np
from scipy import stats

MIN_FOLDS_FOR_ANY_TEST = 3
MIN_FOLDS_FOR_WILCOXON_CROSSCHECK = 6
MIN_FOLDS_FOR_TTEST_CROSSCHECK = 8
MAX_EXACT_FOLDS = 15  # 2^15 = 32768 sign patterns, still fast; beyond this, sample


def _exact_permutation_p_value(diffs: np.ndarray) -> float:
    n = len(diffs)
    observed = abs(diffs.mean())
    count_extreme = 0
    total = 0
    for signs in itertools.product([-1, 1], repeat=n):
        perm_mean = abs((diffs * np.array(signs)).mean())
        if perm_mean >= observed - 1e-12:
            count_extreme += 1
        total += 1
    return count_extreme / total


def _monte_carlo_permutation_p_value(diffs: np.ndarray, n_iter: int, rng) -> float:
    n = len(diffs)
    observed = abs(diffs.mean())
    count_extreme = 0
    for _ in range(n_iter):
        signs = rng.choice([-1, 1], size=n)
        perm_mean = abs((diffs * signs).mean())
        if perm_mean >= observed - 1e-12:
            count_extreme += 1
    return count_extreme / n_iter


def _bootstrap_ci(diffs: np.ndarray, n_iter: int, ci_level: float, rng):
    n = len(diffs)
    boot_means = np.empty(n_iter)
    for i in range(n_iter):
        sample = rng.choice(diffs, size=n, replace=True)
        boot_means[i] = sample.mean()
    alpha = (1 - ci_level) / 2
    return float(np.percentile(boot_means, alpha * 100)), float(np.percentile(boot_means, (1 - alpha) * 100))


def evaluate_paired_comparison(fold_metric_diffs: list, n_monte_carlo: int = 5000, ci_level: float = 0.95, random_seed: int = 42):
    """
    fold_metric_diffs: list of (candidate_metric - baseline_metric) per fold,
    already oriented so a positive value means the candidate improved on the
    baseline for that fold.

    Returns a dict matching the columns the experiment log needs:
    statistical_test_used, raw_p_value, ci_lower, ci_upper,
    insufficient_sample, dependence_warning, crosscheck_notes.
    """
    n = len(fold_metric_diffs)
    result = {
        "statistical_test_used": None,
        "raw_p_value": None,
        "ci_lower": None,
        "ci_upper": None,
        "insufficient_sample": False,
        "dependence_warning": True,  # always -- see module docstring
        "crosscheck_notes": "",
    }

    if n < MIN_FOLDS_FOR_ANY_TEST:
        result["statistical_test_used"] = "insufficient_sample"
        result["insufficient_sample"] = True
        return result

    diffs = np.array(fold_metric_diffs, dtype=float)
    rng = np.random.default_rng(random_seed)

    if n <= MAX_EXACT_FOLDS:
        p_value = _exact_permutation_p_value(diffs)
        result["statistical_test_used"] = "permutation_exact"
    else:
        p_value = _monte_carlo_permutation_p_value(diffs, n_monte_carlo, rng)
        result["statistical_test_used"] = "permutation_monte_carlo"
    result["raw_p_value"] = float(p_value)

    ci_lower, ci_upper = _bootstrap_ci(diffs, n_monte_carlo, ci_level, rng)
    result["ci_lower"] = ci_lower
    result["ci_upper"] = ci_upper

    crosschecks = []
    if n >= MIN_FOLDS_FOR_WILCOXON_CROSSCHECK and np.any(diffs != 0):
        try:
            _, wilcoxon_p = stats.wilcoxon(diffs)
            crosschecks.append(f"wilcoxon_crosscheck_p={wilcoxon_p:.4f}")
        except ValueError as e:
            crosschecks.append(f"wilcoxon_crosscheck_skipped({e})")

    if n >= MIN_FOLDS_FOR_TTEST_CROSSCHECK:
        try:
            _, shapiro_p = stats.shapiro(diffs)
            if shapiro_p > 0.05:  # fails to reject normality -- t-test assumption plausible, not "proven"
                _, t_p = stats.ttest_1samp(diffs, popmean=0.0)
                crosschecks.append(f"paired_t_test_crosscheck_p={t_p:.4f} (shapiro_p={shapiro_p:.3f})")
            else:
                crosschecks.append(
                    f"paired_t_test_crosscheck_skipped (shapiro_p={shapiro_p:.3f} suggests non-normal diffs)"
                )
        except ValueError as e:
            crosschecks.append(f"paired_t_test_crosscheck_skipped({e})")

    result["crosscheck_notes"] = "; ".join(crosschecks) if crosschecks else "no crosschecks run (n too small)"
    return result
