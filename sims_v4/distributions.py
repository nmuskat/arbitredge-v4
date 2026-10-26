"""
sims_v4/distributions.py -- discrete score-margin and total distributions.

P(margin = m) ∝ N_discrete(m; mu, sigma) * w(|m|), where w is a key-number
weight learned from TRAINING margins only:
    w(k) = empirical frequency of |margin| = k  /  smooth (normal) frequency
This reproduces the NFL's spikes at 3, 7, 10, 6, 14 and the rarity of ties,
which matter for spread pushes and buying/selling half-points.
Totals use the same construction with weights on the total value.
"""

import math

import numpy as np

M_RANGE = np.arange(-80, 81)
T_RANGE = np.arange(0, 131)


def _norm_cdf(x):
    return 0.5 * (1.0 + np.vectorize(math.erf)(x / math.sqrt(2.0)))


def discrete_normal(values, mu, sigma):
    p = _norm_cdf((values + 0.5 - mu) / sigma) - _norm_cdf((values - 0.5 - mu) / sigma)
    return p / p.sum()


def learn_weights(observed, values, sigma_smooth, shrink=5.0, absolute=True, cap=4.0):
    """w(k) = (count_k + s) / (expected_k + s): ratio of empirical to smooth
    frequency with s pseudo-observations shrinking rare values toward 1."""
    obs = np.rint(np.abs(observed) if absolute else np.asarray(observed)).astype(int)
    n = len(obs)
    center = 0.0 if absolute else float(np.mean(obs))
    smooth = discrete_normal(values, center, sigma_smooth)
    expected = {}
    for v, p in zip(values, smooth):
        k = abs(int(v)) if absolute else int(v)
        expected[k] = expected.get(k, 0.0) + n * p
    counts = {}
    for o in obs:
        counts[int(o)] = counts.get(int(o), 0) + 1
    return {k: float(np.clip((counts.get(k, 0) + shrink) / (e + shrink), 0.02, cap)) for k, e in expected.items()}


class KeyNumberModel:
    def __init__(self, values, absolute):
        self.values, self.absolute, self.w = values, absolute, None

    def fit(self, observed, sigma_smooth):
        self.w = learn_weights(observed, self.values, sigma_smooth, absolute=self.absolute)
        return self

    def fit_ipf(self, mus, observed, sigma, iters=40, shrink=5.0, cap=4.0):
        """Iterative proportional fitting: adjust w until the summed predicted
        probability of each key equals its observed count, given each training
        game's own mean (mus). Calibrates conditional key-number mass."""
        keys = np.abs(self.values) if self.absolute else self.values
        obs = np.rint(np.abs(observed) if self.absolute else np.asarray(observed)).astype(int)
        counts = {}
        for o in obs:
            counts[int(o)] = counts.get(int(o), 0) + 1
        base = np.array([discrete_normal(self.values, m, sigma) for m in mus])      # games x values
        uniq = sorted(set(int(k) for k in keys))
        idx = {k: np.where(keys == k)[0] for k in uniq}
        w = {k: 1.0 for k in uniq}
        for _ in range(iters):
            wv = np.array([w[int(k)] for k in keys])
            p = base * wv
            p /= p.sum(axis=1, keepdims=True)
            pred = {k: p[:, idx[k]].sum() for k in uniq}
            for k in uniq:
                w[k] = float(np.clip(w[k] * (counts.get(k, 0) + shrink) / (pred[k] + shrink), 0.02, cap))
        self.w = w
        return self

    def pmf(self, mu, sigma):
        base = discrete_normal(self.values, mu, sigma)
        keys = np.abs(self.values) if self.absolute else self.values
        p = base * np.array([self.w.get(int(k), 1.0) for k in keys])
        p = p / p.sum()
        return dict(zip(self.values.tolist(), p.tolist()))


def margin_probs(pmf, home_line):
    """home_line: handicap applied to home (e.g. -3). Returns cover, push, fail for HOME."""
    cover = sum(p for m, p in pmf.items() if m + home_line > 1e-9)
    push = sum(p for m, p in pmf.items() if abs(m + home_line) <= 1e-9)
    return cover, push, 1.0 - cover - push


def moneyline_probs(pmf):
    win = sum(p for m, p in pmf.items() if m > 0)
    tie = pmf.get(0, 0.0)
    return win, tie, 1.0 - win - tie


def total_probs(pmf, line):
    over = sum(p for t, p in pmf.items() if t > line + 1e-9)
    push = sum(p for t, p in pmf.items() if abs(t - line) <= 1e-9)
    return over, push, 1.0 - over - push
