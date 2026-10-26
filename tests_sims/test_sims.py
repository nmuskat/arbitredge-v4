import math
import os

import numpy as np
import pandas as pd
import pytest

from sims_v4 import distributions as D
from sims_v4 import features as F
from sims_v4 import model as M


def synthetic_games(seasons=(2006, 2007, 2008, 2009, 2010), seed=3):
    """Round-robin-ish schedule with true strengths; fields match nfl_data.load_games."""
    rng = np.random.default_rng(seed)
    teams = [f"T{i:02d}" for i in range(12)]
    strength = {t: rng.normal(0, 4) for t in teams}
    rows, gid = [], 0
    for s in seasons:
        for w in range(1, 12):
            order = rng.permutation(teams)
            day = pd.Timestamp(f"{s}-09-07") + pd.Timedelta(days=7 * (w - 1))
            for i in range(0, len(order), 2):
                h, a = order[i], order[i + 1]
                mu = strength[h] - strength[a] + 2.0
                margin = int(round(rng.normal(mu, 13)))
                total = int(max(3, round(rng.normal(44, 13))))
                hs = max(0, (total + margin) // 2)
                as_ = max(0, total - hs)
                gid += 1
                rows.append({"game_id": f"g{gid}", "season": s, "week": w, "game_type": "REG",
                             "home_team": h, "away_team": a, "home_score": hs, "away_score": as_,
                             "result": hs - as_, "total": hs + as_, "neutral": 0, "played": True,
                             "game_date": day.date(), "kickoff_utc": day.tz_localize("UTC"),
                             "home_rest": 7, "away_rest": 7, "home_qb_id": f"{h}q", "away_qb_id": f"{a}q",
                             "spread_line": round(mu * 2) / 2, "mkt_margin": round(mu * 2) / 2,
                             "total_line": 44.5, "indoor": 0})
    g = pd.DataFrame(rows)
    epa = pd.concat([
        pd.DataFrame({"game_id": g.game_id, "team": g.home_team, "off_plays": 62, "off_epa": g.result / 60,
                      "off_sr": 0.43, "off_pass_rate": 0.6, "def_epa": -g.result / 60, "def_sr": 0.43}),
        pd.DataFrame({"game_id": g.game_id, "team": g.away_team, "off_plays": 62, "off_epa": -g.result / 60,
                      "off_sr": 0.43, "off_pass_rate": 0.6, "def_epa": g.result / 60, "def_sr": 0.43})])
    return g, epa


# ---------------- leakage ----------------
def test_features_ignore_future_results():
    g, e = synthetic_games()
    f1 = F.build_features(g, e)
    cut = g.game_date.iloc[len(g) // 2]
    g2 = g.copy()
    fut = g2.game_date >= cut
    g2.loc[fut, ["home_score", "away_score"]] = g2.loc[fut, ["away_score", "home_score"]].to_numpy() + 30
    g2.loc[fut, "result"] = g2.loc[fut, "home_score"] - g2.loc[fut, "away_score"]
    f2 = F.build_features(g2, e)
    cols = ["elo_diff", "epa_net_diff", "pts_net_diff", "h_pf", "a_pa"]
    past = f1.game_date <= cut          # games ON the cut date must also be unaffected
    pd.testing.assert_frame_equal(f1.loc[past, cols].reset_index(drop=True), f2.loc[past, cols].reset_index(drop=True))
    assert not f1.loc[~past, "elo_diff"].equals(f2.loc[~past, "elo_diff"])


def test_first_game_has_league_default_features():
    g, e = synthetic_games()
    f = F.build_features(g, e)
    first = f.iloc[0]
    assert first.elo_diff == 0 and first.h_n == 0 and first.epa_net_diff == 0


def test_season_regression_of_elo():
    g, e = synthetic_games(seasons=(2006, 2007))
    f = F.build_features(g, e)
    sd06 = f[f.season == 2006].groupby("home_team").elo_home.last().std()
    sd07_first = f[(f.season == 2007) & (f.week == 1)].elo_home.std()
    assert sd07_first < sd06


# ---------------- distributions ----------------
def test_pmf_sums_to_one_and_mean_close():
    km = D.KeyNumberModel(D.M_RANGE, True)
    km.w = {k: 1.0 for k in range(81)}
    pmf = km.pmf(3.0, 13.5)
    assert sum(pmf.values()) == pytest.approx(1.0)
    assert sum(m * p for m, p in pmf.items()) == pytest.approx(3.0, abs=0.05)


def test_ipf_learns_key_numbers_and_calibrates_counts():
    rng = np.random.default_rng(0)
    mus = rng.normal(0, 6, 4000)
    raw = rng.normal(mus, 13)
    obs = np.where(rng.random(4000) < 0.12, np.sign(raw) * 3, np.rint(raw)).astype(int)   # inject mass at +-3
    km = D.KeyNumberModel(D.M_RANGE, True).fit_ipf(mus, obs, 13.0)
    assert km.w[3] > 2.0 and abs(km.w[5] - 1.0) < 0.35
    pred3 = sum(km.pmf(m, 13.0)[3] + km.pmf(m, 13.0)[-3] for m in mus[:1000])
    act3 = int((np.abs(obs[:1000]) == 3).sum())
    assert abs(pred3 - act3) < 0.15 * act3 + 10


def test_margin_moneyline_total_probs():
    pmf = {-3: 0.2, 0: 0.1, 3: 0.3, 7: 0.4}
    assert D.margin_probs(pmf, -3.0) == pytest.approx((0.4, 0.3, 0.3))
    assert D.moneyline_probs(pmf) == pytest.approx((0.7, 0.1, 0.2))
    assert D.total_probs({40: 0.5, 44: 0.2, 50: 0.3}, 44) == pytest.approx((0.3, 0.2, 0.5))


# ---------------- model ----------------
def test_ridge_recovers_known_coefficients():
    rng = np.random.default_rng(1)
    df = pd.DataFrame({"x1": rng.normal(0, 1, 5000), "x2": rng.normal(0, 2, 5000)})
    y = 1.0 + 3.0 * df.x1 - 0.5 * df.x2 + rng.normal(0, 1, 5000)
    r = M.Ridge(["x1", "x2"], lam=0.0).fit(df, y.to_numpy())
    c = r.coefficients()
    assert c["x1"] == pytest.approx(3.0, abs=0.05) and c["x2"] == pytest.approx(-0.5, abs=0.05)
    assert r.sigma == pytest.approx(1.0, abs=0.05)
    mean, se = r.predict(df.head(3))
    assert (se > 0).all() and (se < 0.1).all()


def test_fit_season_uses_only_prior_seasons():
    g, e = synthetic_games()
    f = F.build_features(g, e)
    sm = M.fit_season(f, 2010)
    assert sm.meta["train_games"] == int(((f.season >= 2008) & (f.season < 2010)).sum())
    f2 = f.copy()
    f2.loc[f2.season == 2010, "result"] += 50      # tamper with the target season
    sm2 = M.fit_season(f2, 2010)
    assert np.allclose(sm.margin.coef, sm2.margin.coef)


def test_predictions_carry_distributions_and_uncertainty():
    g, e = synthetic_games()
    f = F.build_features(g, e)
    sm = M.fit_season(f, 2010)
    preds = M.predict_games(sm, f[f.season == 2010].head(5), tau=1.5)
    for p in preds:
        assert sum(p["margin_pmf"].values()) == pytest.approx(1.0)
        assert p["se_margin"] >= 1.5 and 0 < p["p_home_win"] < 1


# ---------------- real data smoke test (skipped when data absent) ----------------
REAL = "data/derived/features.parquet"


@pytest.mark.skipif(not os.path.exists(REAL), reason="real nflverse features not downloaded")
def test_real_data_anchored_model_is_near_market_not_magic():
    from sims_v4 import evaluate as E
    f = pd.read_parquet(REAL)
    _, df = E.score_season(f, 2024, "anchored")
    s = E.summarize(df, n_boot=200)
    # sanity: a market-anchored model should be close to the market, not wildly better or worse
    assert abs(s["ml"]["logloss_model"] - s["ml"]["logloss_market"]) < 0.03
    assert 12.0 < s["margin_rmse_model"] < 15.0


def test_same_day_early_result_not_visible_to_late_kickoff():
    g, e = synthetic_games(seasons=(2006,))
    day = g.game_date.iloc[30]
    same = g.index[g.game_date == day]
    early, late = same[0], same[1]
    # give the two games a shared team and staggered kickoffs (1 pm vs 4:25 pm ET)
    g.loc[late, "home_team"] = g.loc[early, "home_team"]
    g.loc[early, "kickoff_utc"] = pd.Timestamp(f"{day} 17:00", tz="UTC")
    g.loc[late, "kickoff_utc"] = pd.Timestamp(f"{day} 20:25", tz="UTC")
    g = g.sort_values(["kickoff_utc", "game_id"]).reset_index(drop=True)
    f1 = F.build_features(g, e)
    g2 = g.copy()
    i = g2.index[g2.game_id == g.loc[g.kickoff_utc == pd.Timestamp(f"{day} 17:00", tz="UTC"), "game_id"].iloc[0]][0]
    g2.loc[i, ["home_score", "result"]] = [g2.loc[i, "away_score"] + 40, 40]
    f2 = F.build_features(g2, e)
    late_id = g.loc[g.kickoff_utc == pd.Timestamp(f"{day} 20:25", tz="UTC"), "game_id"].iloc[0]
    a = f1.set_index("game_id").loc[late_id, ["elo_home", "h_pf"]]
    b = f2.set_index("game_id").loc[late_id, ["elo_home", "h_pf"]]
    assert a.equals(b)
