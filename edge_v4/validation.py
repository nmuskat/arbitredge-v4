"""
edge_v4/validation.py -- statistics for proving (or failing to prove) an edge.

Everything here is pure and deterministic (seeded bootstrap). Bets are
dicts with: placed_at (ISO/epoch, sortable), stake, american, result
('W','L','P'), and optionally close_american / close_fair_prob,
p_model, sport, market.
"""

import math
import random

from edge_v4 import pricing


def pnl(bet):
    r = bet["result"]
    if r == "W":
        return bet["stake"] * pricing.net_profit_per_unit(bet["american"])
    if r == "L":
        return -bet["stake"]
    if r == "P":
        return 0.0
    raise ValueError(f"unknown result {r!r}")


def record(bets):
    w = sum(b["result"] == "W" for b in bets)
    l = sum(b["result"] == "L" for b in bets)
    p = sum(b["result"] == "P" for b in bets)
    staked = sum(b["stake"] for b in bets)
    profit = sum(pnl(b) for b in bets)
    return {"n": len(bets), "W": w, "L": l, "P": p, "staked": staked,
            "profit": profit, "roi": profit / staked if staked else None}


def max_drawdown(bets):
    eq = peak = dd = 0.0
    for b in sorted(bets, key=lambda b: b["placed_at"]):
        eq += pnl(b)
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return dd


def longest_losing_streak(bets):
    best = cur = 0
    for b in sorted(bets, key=lambda b: b["placed_at"]):
        if b["result"] == "L":
            cur += 1
            best = max(best, cur)
        elif b["result"] == "W":
            cur = 0
    return best


def bootstrap_roi_ci(bets, n_boot=5000, alpha=0.05, seed=7):
    """Percentile CI for ROI and the one-sided bootstrap p-value of ROI <= 0."""
    if not bets:
        return None
    rng = random.Random(seed)
    pnls = [pnl(b) for b in bets]
    stakes = [b["stake"] for b in bets]
    n = len(bets)
    rois = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        s = sum(stakes[i] for i in idx)
        rois.append(sum(pnls[i] for i in idx) / s if s else 0.0)
    rois.sort()
    lo = rois[int(alpha / 2 * n_boot)]
    hi = rois[int((1 - alpha / 2) * n_boot) - 1]
    # (k+1)/(n+1): a bootstrap can never certify p == 0, which would make
    # any multiple-testing adjustment a no-op.
    p_le_zero = (sum(r <= 0 for r in rois) + 1) / (n_boot + 1)
    return {"roi_lo": lo, "roi_hi": hi, "p_roi_le_0": p_le_zero, "n": n}


def clv(bet):
    """
    Closing-line value as probability points: fair closing probability of
    the selection minus the break-even probability of the price taken.
    Positive = beat the close. Needs a no-vig closing probability; returns
    None when no close was OBSERVED (never imputed).
    """
    cfp = bet.get("close_fair_prob")
    if cfp is None:
        return None
    return cfp - pricing.implied_prob(bet["american"])


def brier(probs, outcomes):
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


def log_loss(probs, outcomes, eps=1e-12):
    return -sum(o * math.log(max(p, eps)) + (1 - o) * math.log(max(1 - p, eps))
                for p, o in zip(probs, outcomes)) / len(probs)


def calibration_table(probs, outcomes, bins=10):
    rows = []
    for k in range(bins):
        lo, hi = k / bins, (k + 1) / bins
        sel = [(p, o) for p, o in zip(probs, outcomes) if lo <= p < hi or (k == bins - 1 and p == 1.0)]
        if sel:
            rows.append({"bin": (lo, hi), "n": len(sel),
                         "mean_pred": sum(p for p, _ in sel) / len(sel),
                         "observed": sum(o for _, o in sel) / len(sel)})
    return rows


def holm_adjust(pvalues):
    """Holm-Bonferroni adjusted p-values (family-wise error control across strategies)."""
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    m = len(pvalues)
    adj = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adj[i] = running
    return adj


class LeakageError(Exception):
    pass


def assert_point_in_time(rows, decision_ts_key="decided_at", input_ts_keys=("odds_ts", "features_ts", "injury_ts")):
    """Every input used for a decision must be timestamped at or before the decision."""
    for r in rows:
        for k in input_ts_keys:
            if r.get(k) is not None and r[k] > r[decision_ts_key]:
                raise LeakageError(f"{k}={r[k]} is after {decision_ts_key}={r[decision_ts_key]} (row {r.get('id')})")


def walk_forward_splits(items, key, train_min, test_size, step=None):
    """
    Chronological expanding-window splits. Yields (train, test) with every
    train item strictly earlier than every test item.
    """
    s = sorted(items, key=key)
    step = step or test_size
    start = train_min
    while start + test_size <= len(s):
        train, test = s[:start], s[start:start + test_size]
        if key(train[-1]) >= key(test[0]):
            # equal timestamps straddling the boundary would leak; push boundary forward
            cut = start
            while cut < len(s) and key(s[cut]) <= key(train[-1]):
                cut += 1
            train, test = s[:cut], s[cut:cut + test_size]
            if not test:
                return
        yield train, test
        start += step


def edge_evidence_summary(bets, n_strategies_tested=1, n_boot=5000):
    """The headline a dashboard may show. 'proven' requires every criterion."""
    rec = record(bets)
    ci = bootstrap_roi_ci(bets, n_boot=n_boot)
    clvs = [c for c in (clv(b) for b in bets) if c is not None]
    p_adj = None if ci is None else min(1.0, ci["p_roi_le_0"] * n_strategies_tested)
    criteria = {
        "sample_size>=500": rec["n"] >= 500,
        "roi_ci_lower>0": bool(ci and ci["roi_lo"] > 0),
        "multiple_testing_adjusted_p<0.05": p_adj is not None and p_adj < 0.05,
        "mean_clv>0_with_>=80%_close_coverage": bool(clvs) and len(clvs) >= 0.8 * rec["n"] and sum(clvs) / len(clvs) > 0,
    }
    return {"record": rec, "roi_ci": ci, "p_adjusted": p_adj,
            "mean_clv": sum(clvs) / len(clvs) if clvs else None, "clv_coverage": len(clvs) / rec["n"] if rec["n"] else 0,
            "max_drawdown": max_drawdown(bets), "longest_losing_streak": longest_losing_streak(bets),
            "criteria": criteria, "edge_status": "SUPPORTED" if all(criteria.values()) else "NOT PROVEN"}
