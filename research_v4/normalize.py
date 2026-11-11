"""
research_v4/normalize.py -- A1: put every book's (point, price) on one comparable scale.  RESEARCH ONLY.

For a two-way quote (side A at point P with price a, side B with price b):
  1. remove the vig (multiplicative) -> p_A = probability side A wins GIVEN no push at P;
  2. find the implied mean mu (home margin, or total points) such that a push-aware discrete distribution
     gives P(A covers at P | no push, mu) = p_A;
  3. that mu is the book's IMPLIED FAIR LINE. Two books at different numbers/prices become comparable:
     compare their mu, or their probability at any common reference number.
Distributions: NFL margin/total = sims_v4 key-number model (fit on NFL 2008-2025, validated out of sample).
CFB: same construction with key weights fit on CFB final margins/totals supplied by the caller and a sigma
estimated from development data -- LOW CONFIDENCE (one season), flagged in every output.
"""

import math

import numpy as np

from edge_v4 import pricing
from sims_v4 import distributions as D

GRID_STEP = 0.05


class Normalizer:
    def __init__(self, kind, sigma, weights, values, confidence):
        self.kind, self.sigma, self.confidence = kind, sigma, confidence     # kind: margin | total
        self.values = np.asarray(values)
        keys = np.abs(self.values) if kind == "margin" else self.values
        w = np.array([weights.get(int(k), 1.0) for k in keys])
        lo, hi = (-75.0, 75.0) if kind == "margin" else (10.0, 115.0)
        self.mus = np.arange(lo, hi + 1e-9, GRID_STEP)
        base = np.array([D.discrete_normal(self.values, m, sigma) for m in self.mus]) * w
        self.pmf = base / base.sum(axis=1, keepdims=True)                     # mus x values
        self.cdf = np.cumsum(self.pmf, axis=1)

    def _probs(self, threshold_idx_value):
        """(P(value > t), P(value == t)) for every mu, t a half- or whole-number threshold."""
        t = threshold_idx_value
        gt = self.values > t + 1e-9
        eq = np.abs(self.values - t) <= 1e-9
        return self.pmf[:, gt].sum(axis=1), self.pmf[:, eq].sum(axis=1)

    def cover_no_push(self, side, point):
        """P(side covers | no push) for every mu on the grid.
        margin: side home with home line `point` covers when margin > -point; away (own points) when margin < point.
        total: over covers when total > point; under when total < point."""
        if self.kind == "margin":
            t = -point if side == "home" else point
            gt, eq = self._probs(t)
            win = gt if side == "home" else 1.0 - gt - eq
        else:
            gt, eq = self._probs(point)
            win = gt if side == "over" else 1.0 - gt - eq
        return win / np.clip(1.0 - eq, 1e-9, None), eq

    def implied_mu(self, side, point, p_nopush):
        """Mean (home margin / total) at which `side` at `point` wins with probability p_nopush (no push)."""
        f, _ = self.cover_no_push(side, point)
        order = np.argsort(f)
        return float(np.interp(p_nopush, f[order], self.mus[order]))

    def prob_at(self, mu, side, point):
        """(P(win), P(push)) of `side` at `point` given mean mu."""
        f, eq = self.cover_no_push(side, point)
        i = int(np.clip(round((mu - self.mus[0]) / GRID_STEP), 0, len(self.mus) - 1))
        return float(f[i] * (1 - eq[i])), float(eq[i])

    def quote(self, side_a, point_a, price_a, side_b, point_b, price_b):
        """Normalize one two-way quote. Returns dict(mu, p_a_nopush) or None if the pair is unusable."""
        if point_a is None or price_a is None or price_b is None:
            return None
        if self.kind == "margin" and point_b is not None and abs(point_a + point_b) > 1e-9:
            return None                                   # mismatched spread pair (stale / alt)
        if self.kind == "total" and point_b is not None and abs(point_a - point_b) > 1e-9:
            return None
        try:
            pa, _pb = pricing.devig([price_a, price_b])
        except ValueError:
            return None
        if self.kind == "margin" and abs(point_a) > 40:
            return None                                   # extreme spreads: distribution tails unreliable -> excluded
        mu = self.implied_mu(side_a, point_a, pa)
        if mu <= self.mus[0] + 1 or mu >= self.mus[-1] - 1:
            return None                                   # at the grid edge: not a valid inversion
        return {"mu": mu, "p_a_nopush": pa}


def nfl():
    import json
    import os
    d = json.load(open(os.path.join(os.path.dirname(D.__file__), "nfl_distribution_2026.json")))
    mw = {int(k): v for k, v in d["margin_weights"].items()}
    tw = {int(k): v for k, v in d["total_weights"].items()}
    return (Normalizer("margin", d["margin_sigma"], mw, D.M_RANGE, "validated (NFL 2008-2025)"),
            Normalizer("total", d["total_sigma"], tw, D.T_RANGE, "validated (NFL 2008-2025)"))


def cfb(final_margins, final_totals, margin_sigma, total_sigma):
    """CFB normalizers from supplied final scores (development data only). LOW CONFIDENCE."""
    mw = D.learn_weights(np.asarray(final_margins), D.M_RANGE, margin_sigma, absolute=True)
    tw = {}                      # CFB totals: no key-number structure is identifiable from one season (weights were noise)
    return (Normalizer("margin", margin_sigma, mw, D.M_RANGE, "LOW CONFIDENCE (CFB 2026 only)"),
            Normalizer("total", total_sigma, tw, D.T_RANGE, "LOW CONFIDENCE (CFB 2026 only)"))
