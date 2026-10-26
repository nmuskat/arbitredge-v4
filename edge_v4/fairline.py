"""
edge_v4/fairline.py -- fair prices at ANY line from a consensus at one line.

Given the sharp no-vig probability that HOME covers the consensus spread L0
(conditional on no push), find the mean margin mu whose discrete
key-number distribution (Engine C) reproduces it, then price any other
line, the moneyline and push probabilities from that same distribution.
Totals likewise. This is how a book hanging -2.5 is compared with a
consensus at -3 without pretending half-points are worth a constant.
"""

from sims_v4 import distributions as D


def _solve_mu(prob_at, target, lo=-40.0, hi=40.0, iters=60):
    """prob_at(mu) increasing in mu; find mu with prob_at(mu) == target."""
    for _ in range(iters):
        mid = (lo + hi) / 2
        if prob_at(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


class SpreadFair:
    def __init__(self, kn_model, sigma, consensus_home_line, fair_home_cover):
        def p(mu):
            c, _, f = D.margin_probs(kn_model.pmf(mu, sigma), consensus_home_line)
            return c / (c + f)
        self.mu = _solve_mu(p, fair_home_cover)
        self.pmf = kn_model.pmf(self.mu, sigma)

    def side_probs(self, side, line):
        """(p_win, p_push) for `side` at its own line (home -2.5 => side='home', line=-2.5)."""
        home_line = line if side == "home" else -line
        c, pu, f = D.margin_probs(self.pmf, home_line)
        return (c, pu) if side == "home" else (f, pu)

    def moneyline(self, side):
        w, t, l = D.moneyline_probs(self.pmf)
        return (w, t) if side == "home" else (l, t)


class TotalFair:
    def __init__(self, kn_model, sigma, consensus_total, fair_over):
        def p(mu):
            o, _, u = D.total_probs(kn_model.pmf(mu, sigma), consensus_total)
            return o / (o + u)
        self.mu = _solve_mu(p, fair_over, 10.0, 90.0)
        self.pmf = kn_model.pmf(self.mu, sigma)

    def side_probs(self, side, line):
        o, pu, u = D.total_probs(self.pmf, line)
        return (o, pu) if side == "over" else (u, pu)
