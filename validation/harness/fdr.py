"""
validation/harness/fdr.py

Benjamini-Hochberg false discovery rate correction, applied across a
"family" of comparisons run together (same fdr_family_id). Standalone,
dependency-light implementation (no scipy.stats.false_discovery_control
dependency, for portability across the numpy/scipy version range this
project targets).
"""


def benjamini_hochberg(p_values: list, alpha: float = 0.05):
    """
    p_values: list of raw p-values (may contain None for entries where no
    valid test was run -- those are passed through as not-significant
    without participating in the correction's rank ordering).

    Returns a list of (adjusted_p_value_or_None, is_significant_or_None),
    same length and order as the input.
    """
    n = len(p_values)
    indexed = [(i, p) for i, p in enumerate(p_values) if p is not None]
    indexed.sort(key=lambda x: x[1])

    m = len(indexed)
    adjusted = [None] * n
    significant = [None] * n

    if m == 0:
        return list(zip(adjusted, significant))

    # Standard BH step-up procedure
    raw_adjusted = [0.0] * m
    prev = 1.0
    for rank in range(m, 0, -1):
        i, p = indexed[rank - 1]
        adj = min(prev, p * m / rank)
        raw_adjusted[rank - 1] = adj
        prev = adj

    for rank, (i, p) in enumerate(indexed, start=1):
        adj = raw_adjusted[rank - 1]
        adjusted[i] = adj
        significant[i] = 1 if adj <= alpha else 0

    return list(zip(adjusted, significant))
