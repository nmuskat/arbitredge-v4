"""
behavioral/_stats.py

Shared slate-relative standardization -- the single, uniform methodology
both models use to put differently-scaled features on a comparable
footing before an equal-weighted sum. This is a MECHANICAL procedure
(computed fresh from whatever batch of games is passed in), never a
fitted parameter, never a population statistic sourced from anywhere
else. A degenerate slate (fewer than 2 real values, or zero variance)
produces a neutral 0 contribution for every game -- never fabricated,
never a crash.
"""

import math


def slate_zscore(values):
    """values: list of raw numeric values (entries may be None for
    missing data). Returns a same-length, same-order list of z-scores,
    with None entries and degenerate (zero-variance or <2 real values)
    slates treated as a neutral 0.0 contribution."""
    real_values = [v for v in values if v is not None]
    if len(real_values) < 2:
        return [0.0 for _ in values]
    mean = sum(real_values) / len(real_values)
    variance = sum((v - mean) ** 2 for v in real_values) / len(real_values)
    stdev = math.sqrt(variance)
    if stdev == 0:
        return [0.0 for _ in values]
    return [0.0 if v is None else (v - mean) / stdev for v in values]


def slate_percentile_ranks(scores):
    """scores: list of numeric scores. Returns percentile ranks (1-100),
    or None for every entry if the slate has fewer than 2 games -- a
    relative rank is meaningless with nothing to compare against, and
    reporting a trivial 100th percentile for a lone game would be
    misleading, not merely uninformative.

    TIE CONVENTION: AVERAGE RANK (fractional ranking), the standard
    statistical convention -- tied scores receive the mean of the
    ordinal ranks their group spans (e.g. two games tied for ranks 3-4
    both receive rank 3.5), rather than DENSE RANK (which would give
    both rank 3 and compress every subsequent rank down by one). Average
    rank is the right choice here specifically because it preserves the
    correct percentile SPACING for every OTHER game in the slate, not
    just the tied ones -- dense rank would distort the relative standing
    of every game below a tie, which average rank does not.
    """
    n = len(scores)
    if n < 2:
        return [None] * n

    sorted_indices = sorted(range(n), key=lambda i: scores[i])

    ordinal_ranks = [0.0] * n  # 1-indexed
    i = 0
    while i < n:
        j = i
        while j + 1 < n and scores[sorted_indices[j + 1]] == scores[sorted_indices[i]]:
            j += 1
        average_rank = sum(range(i + 1, j + 2)) / (j - i + 1)
        for k in range(i, j + 1):
            ordinal_ranks[sorted_indices[k]] = average_rank
        i = j + 1

    return [r / n * 100 for r in ordinal_ranks]

def reference_zscore(value, reference_values):
    """
    Standardize one value against a fixed reference distribution.

    The value being evaluated is NOT inserted into the reference.
    Missing values and degenerate reference distributions contribute
    a neutral 0.0, matching slate_zscore's existing convention.
    """
    if value is None:
        return 0.0

    real_values = [v for v in reference_values if v is not None]

    if len(real_values) < 2:
        return 0.0

    mean = sum(real_values) / len(real_values)
    variance = sum((v - mean) ** 2 for v in real_values) / len(real_values)
    stdev = math.sqrt(variance)

    if stdev == 0:
        return 0.0

    return (value - mean) / stdev


def reference_percentile_rank(score, reference_scores):
    """
    Rank one score against a fixed reference distribution without
    inserting the evaluated score into that distribution.

    Ties use the midpoint of the tied reference rank interval.
    """
    real_scores = [s for s in reference_scores if s is not None]
    n = len(real_scores)

    if n < 2 or score is None:
        return None

    below = sum(1 for s in real_scores if s < score)
    equal = sum(1 for s in real_scores if s == score)

    if equal:
        average_rank = below + (equal + 1) / 2
        return average_rank / n * 100

    return below / n * 100
