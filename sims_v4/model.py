"""
sims_v4/model.py -- walk-forward margin / total models.

Two variants, both refit once per season on PRIOR seasons only:
  * independent: margin ~ team features (no market input) -- a genuinely
    separate opinion, used to measure whether we know anything the market
    does not;
  * anchored:    margin ~ market margin + team features -- asks whether team
    features add information BEYOND the closing consensus.
Ridge regression on standardised features; sigma = training residual SD.
"""

from dataclasses import dataclass, field

import numpy as np

from sims_v4 import distributions as D

MARGIN_FEATS = ["elo_diff", "epa_net_diff", "sr_net_diff", "pts_net_diff", "home_field", "rest_diff", "qb_change_diff"]
TOTAL_FEATS = ["tot_pts_env", "tot_epa_env", "tot_plays_env", "indoor"]
TRAIN_FROM = 2008     # 2006-07 are feature warm-up


@dataclass
class Ridge:
    feats: list
    lam: float = 1.0
    coef: np.ndarray = None
    mu: np.ndarray = None
    sd: np.ndarray = None
    sigma: float = None
    cov: np.ndarray = None
    n: int = 0

    def _x(self, df):
        X = (df[self.feats].to_numpy(float) - self.mu) / self.sd
        return np.column_stack([np.ones(len(df)), X])

    def fit(self, df, y):
        A = df[self.feats].to_numpy(float)
        self.mu, self.sd = A.mean(0), A.std(0)
        self.sd[self.sd == 0] = 1.0
        X = self._x(df)
        P = np.eye(X.shape[1]) * self.lam
        P[0, 0] = 0.0
        XtX = X.T @ X + P
        self.coef = np.linalg.solve(XtX, X.T @ y)
        r = y - X @ self.coef
        self.n = len(y)
        self.sigma = float(np.sqrt(r @ r / max(1, len(y) - X.shape[1])))
        self.cov = np.linalg.inv(XtX) * self.sigma ** 2
        return self

    def predict(self, df):
        X = self._x(df)
        mean = X @ self.coef
        se = np.sqrt(np.einsum("ij,jk,ik->i", X, self.cov, X))
        return mean, se

    def coefficients(self):
        return dict(zip(["intercept"] + self.feats, (self.coef / np.r_[1.0, self.sd]).round(5).tolist()))


@dataclass
class SeasonModel:
    season: int
    margin: Ridge
    total: Ridge
    margin_kn: D.KeyNumberModel
    total_kn: D.KeyNumberModel
    variant: str
    meta: dict = field(default_factory=dict)


def fit_season(features, season, variant="independent", lam=1.0):
    tr = features[features.played & (features.season >= TRAIN_FROM) & (features.season < season)]
    tr = tr.dropna(subset=MARGIN_FEATS + TOTAL_FEATS + ["result", "total"])
    mf, tf = list(MARGIN_FEATS), list(TOTAL_FEATS)
    if variant == "anchored":
        tr = tr.dropna(subset=["mkt_margin", "total_line"])
        mf, tf = ["mkt_margin"] + mf, ["total_line"] + tf
    m = Ridge(mf, lam).fit(tr, tr.result.to_numpy(float))
    t = Ridge(tf, lam).fit(tr, tr.total.to_numpy(float))
    mk = D.KeyNumberModel(D.M_RANGE, True).fit_ipf(m.predict(tr)[0], tr.result.to_numpy(), m.sigma)
    tk = D.KeyNumberModel(D.T_RANGE, False).fit_ipf(t.predict(tr)[0], tr.total.to_numpy(), t.sigma)
    return SeasonModel(season, m, t, mk, tk, variant,
                       {"train_games": len(tr), "train_seasons": f"{TRAIN_FROM}-{season - 1}",
                        "margin_sigma": m.sigma, "total_sigma": t.sigma})


def predict_games(sm, games, tau=1.5):
    """Per-game predictions with distributions. tau: extra model-uncertainty SD
    on the mean (points) beyond parameter uncertainty -- an explicit, UNVALIDATED
    policy assumption used for the decision layer's uncertainty interval."""
    g = games.dropna(subset=sm.margin.feats + sm.total.feats)
    mu, mse = sm.margin.predict(g)
    tmu, tse = sm.total.predict(g)
    out = []
    for i, (_, r) in enumerate(g.iterrows()):
        mpmf = sm.margin_kn.pmf(mu[i], sm.margin.sigma)
        tpmf = sm.total_kn.pmf(tmu[i], sm.total.sigma)
        win, tie, loss = D.moneyline_probs(mpmf)
        out.append({"game_id": r.game_id, "season": r.season, "week": r.week, "variant": sm.variant,
                    "home_team": r.home_team, "away_team": r.away_team, "kickoff_utc": r.kickoff_utc,
                    "mu_margin": mu[i], "se_margin": float(np.hypot(mse[i], tau)), "sigma_margin": sm.margin.sigma,
                    "mu_total": tmu[i], "se_total": float(np.hypot(tse[i], tau)), "sigma_total": sm.total.sigma,
                    "p_home_win": win, "p_tie": tie, "margin_pmf": mpmf, "total_pmf": tpmf,
                    "fair_home_spread": -mu[i], "fair_total": tmu[i]})
    return out
