"""
desk_v1/model.py -- ratings, pace, points, market combination and probabilities (all formulas of DESK-v1).

Ratings (per sport, refit for every week from plays of EARLIER weeks only; garbage time excluded):
    EPA(play) = m + o[offence] - d[defence] + h * home_offence + noise
    ridge: minimise sum (EPA - fit)^2 + lambda * sum((o - rho*o_prev)^2 + (d - rho*d_prev)^2); m, h unpenalised.
Pace: expected scrimmage plays of a team = mean of its earlier games, shrunk 50/50 to the league mean.
Expected EPA/play: home offence e_H = m + o[H] - d[A] + h (h = 0 at neutral sites); away e_A = m + o[A] - d[H].
Points: team points = alpha + beta * plays * e  (OLS on earlier games' forward features).
Football-only: mu_fb = p_H - p_A (home margin), tau_fb = p_H + p_A (total).
Desk: mu_desk = mu_mkt + w_s (mu_fb - mu_mkt); tau_desk = tau_mkt + w_t (tau_fb - tau_mkt).
Probabilities: discrete distribution centred on mu_desk (NFL: sims_v4 key-number model; CFB: discrete normal),
sigma x k; P(win), P(push) at the book's own number; EV = p_win * payout(price) - p_loss (push returns stake).
"""
import functools

import numpy as np

from need_v6 import normalize as NZ
from sims_v4 import distributions as D


# ------------------------------------------------------------------------------------------------ ratings
def fit_ratings(plays, teams, lam, prior=None):
    """plays: DataFrame (off, def, home_off, epa), non-garbage. Returns dict(m, h, o{}, d{})."""
    idx = {t: i for i, t in enumerate(teams)}
    T = len(teams)
    n = 2 + 2 * T                                     # [m, h, o..., d...]
    A = np.zeros((n, n))
    b = np.zeros(n)
    if len(plays):
        g = plays.groupby(["off", "def", "home_off"]).epa.agg(["count", "sum"]).reset_index()
        g = g[g["off"].isin(idx) & g["def"].isin(idx)]
        o = 2 + g["off"].map(idx).to_numpy()
        d = 2 + T + g["def"].map(idx).to_numpy()
        ho = g["home_off"].to_numpy().astype(float)
        c = g["count"].to_numpy().astype(float)
        s = g["sum"].to_numpy().astype(float)
        # design row per cell: x[0]=1, x[1]=ho, x[o]=1, x[d]=-1 ; A = sum c x x^T, b = sum s x
        A[0, 0] += c.sum(); A[0, 1] += (c * ho).sum(); A[1, 0] = A[0, 1]; A[1, 1] += (c * ho).sum()
        for col, sign in ((o, 1.0), (d, -1.0)):
            np.add.at(A, (0, col), sign * c); np.add.at(A, (col, 0), sign * c)
            np.add.at(A, (1, col), sign * c * ho); np.add.at(A, (col, 1), sign * c * ho)
        np.add.at(A, (o, o), c); np.add.at(A, (d, d), c)
        np.add.at(A, (o, d), -c); np.add.at(A, (d, o), -c)
        b[0] += s.sum(); b[1] += (s * ho).sum()
        np.add.at(b, o, s); np.add.at(b, d, -s)
    pen = np.zeros(n)
    pen[2:] = lam
    pr = np.zeros(n)
    if prior:
        for t, i in idx.items():
            pr[2 + i] = prior["o"].get(t, 0.0)
            pr[2 + T + i] = prior["d"].get(t, 0.0)
    A += np.diag(pen) + np.eye(n) * 1e-9
    b += pen * pr
    sol = np.linalg.solve(A, b)
    return {"m": float(sol[0]), "h": float(sol[1]), "o": {t: float(sol[2 + i]) for t, i in idx.items()},
            "d": {t: float(sol[2 + T + i]) for t, i in idx.items()}}


def scaled(r, rho):
    return {"o": {k: rho * v for k, v in r["o"].items()}, "d": {k: rho * v for k, v in r["d"].items()}}


def pace_table(plays):
    """{team: (games, mean plays/game)} and league mean, counting ALL scrimmage plays (garbage included)."""
    per = plays.groupby(["game_id", "off"]).size()
    by_team = per.groupby(level="off").agg(["count", "mean"])
    return {t: (int(c), float(m)) for t, (c, m) in by_team.iterrows()}, (float(per.mean()) if len(per) else None)


def exp_plays(team, pace, league, shrink):
    g, m = pace.get(team, (0, None))
    if not g or m is None:
        return league
    return shrink * league + (1 - shrink) * m


def team_features(r, pace, league_pace, shrink, home, away, neutral):
    hh = 0.0 if neutral else r["h"]
    eh = r["m"] + r["o"].get(home, 0.0) - r["d"].get(away, 0.0) + hh
    ea = r["m"] + r["o"].get(away, 0.0) - r["d"].get(home, 0.0)
    return (exp_plays(home, pace, league_pace, shrink), eh, exp_plays(away, pace, league_pace, shrink), ea)


def fit_points(rows):
    """rows: [(plays*epa, points)] -> (alpha, beta) by OLS."""
    x = np.array([a for a, _ in rows])
    y = np.array([b for _, b in rows])
    X = np.vstack([np.ones_like(x), x]).T
    sol, *_ = np.linalg.lstsq(X, y, rcond=None)
    return float(sol[0]), float(sol[1])


def football_lines(feat, alpha, beta):
    ph = alpha + beta * feat[0] * feat[1]
    pa = alpha + beta * feat[2] * feat[3]
    return ph - pa, ph + pa


# ------------------------------------------------------------------------------------------------ distributions
@functools.lru_cache(maxsize=256)
def normalizer(sport, market, sigma):
    """Discrete distribution family with spread sigma (rounded to 0.01 for caching)."""
    if sport == "nfl":
        import json
        import os
        d = json.load(open(os.path.join(os.path.dirname(D.__file__), "nfl_distribution_2026.json")))
        w = {int(k): v for k, v in d["margin_weights" if market == "spread" else "total_weights"].items()}
        return NZ.Normalizer("margin" if market == "spread" else "total", sigma, w,
                             D.M_RANGE if market == "spread" else D.T_RANGE, "desk_v1 NFL")
    return NZ.Normalizer("margin" if market == "spread" else "total", sigma, {},
                         D.M_RANGE if market == "spread" else D.T_RANGE, "desk_v1 CFB")


def nfl_base_sigma(market):
    import json
    import os
    d = json.load(open(os.path.join(os.path.dirname(D.__file__), "nfl_distribution_2026.json")))
    return float(d["margin_sigma" if market == "spread" else "total_sigma"])


def payout(price):
    return price / 100.0 if price > 0 else 100.0 / -price


def side_probs(nz, mu, market, side, point):
    """(p_win, p_push) of `side` at its own `point` (spread: side's line; total: the total)."""
    return nz.prob_at(mu, side, point)


def ev(nz, mu, market, side, point, price):
    pw, pp = side_probs(nz, mu, market, side, point)
    return pw * payout(price) - (1 - pw - pp), pw, pp


def market_mu(nz, market, a_point, a_price, b_point, b_price):
    """No-vig implied mean of a two-way quote under the given distribution family (proxy and T-90 alike)."""
    a, b = ("home", "away") if market == "spread" else ("over", "under")
    z = nz.quote(a, a_point, a_price, b, b_point, b_price)
    return None if z is None else z["mu"]
