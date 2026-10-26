"""
sims_v4/evaluate.py -- walk-forward evaluation of Engine C against the market.

  python -m sims_v4.evaluate --first 2012 --last 2026

For every season S: fit on seasons < S, predict S. Scores (lower is better):
  * win probability:  log loss / Brier vs de-vigged moneyline consensus
  * ATS:  P(home covers the listed spread | no push) vs de-vigged spread odds
  * totals: P(over | no push) vs de-vigged total odds
  * margin / total RMSE vs the listed line
The listed lines are an approximate near-close consensus (games.csv), so
"beating" them is the hardest benchmark there is; a model that does not is
NOT evidence of an edge.
"""

import argparse
import json
import math
import random

import numpy as np
import pandas as pd

from edge_v4 import pricing
from sims_v4 import distributions as D
from sims_v4 import model as M


def _ll(p, y):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _devig_pair(a, b):
    try:
        if pd.isna(a) or pd.isna(b):
            return None
        return pricing.devig([int(a), int(b)])[0]
    except ValueError:
        return None


def score_season(features, season, variant):
    sm = M.fit_season(features, season, variant)
    g = features[(features.season == season) & features.played]
    preds = {p["game_id"]: p for p in M.predict_games(sm, g)}
    rows = []
    for _, r in g.iterrows():
        p = preds.get(r.game_id)
        if p is None:
            continue
        row = {"game_id": r.game_id, "season": season, "variant": variant,
               "mu_margin": p["mu_margin"], "mu_total": p["mu_total"], "result": r.result, "total": r.total,
               "mkt_margin": r.mkt_margin, "total_line": r.total_line}
        # moneyline
        y_win = 1.0 if r.result > 0 else (0.0 if r.result < 0 else None)
        pm = _devig_pair(r.home_moneyline, r.away_moneyline)
        if y_win is not None:
            pw = p["p_home_win"] / (1.0 - p["p_tie"])
            row.update(ml_y=y_win, ml_p_model=pw, ml_p_mkt=pm)
        # ATS at the listed line (home handicap = -spread_line)
        if not pd.isna(r.spread_line):
            c, pu, f = D.margin_probs(p["margin_pmf"], -r.spread_line)
            adj = r.result - r.spread_line
            if abs(adj) > 1e-9:
                row.update(ats_y=1.0 if adj > 0 else 0.0, ats_p_model=c / (c + f),
                           ats_p_mkt=_devig_pair(r.home_spread_odds, r.away_spread_odds), ats_push_p=pu)
            else:
                row.update(ats_push=1, ats_push_p=pu)
        if not pd.isna(r.total_line):
            o, pu, u = D.total_probs(p["total_pmf"], r.total_line)
            adj = r.total - r.total_line
            if abs(adj) > 1e-9:
                row.update(tot_y=1.0 if adj > 0 else 0.0, tot_p_model=o / (o + u),
                           tot_p_mkt=_devig_pair(r.over_odds, r.under_odds))
        rows.append(row)
    return sm, pd.DataFrame(rows)


def summarize(df, seed=11, n_boot=2000):
    out = {"n_games": int(len(df))}
    mm = df.dropna(subset=["mkt_margin"])
    out["margin_rmse_model"] = float(np.sqrt(((mm.mu_margin - mm.result) ** 2).mean()))
    out["margin_rmse_market"] = float(np.sqrt(((mm.mkt_margin - mm.result) ** 2).mean()))
    tt = df.dropna(subset=["total_line"])
    out["total_rmse_model"] = float(np.sqrt(((tt.mu_total - tt.total) ** 2).mean()))
    out["total_rmse_market"] = float(np.sqrt(((tt.total_line - tt.total) ** 2).mean()))
    rng = random.Random(seed)
    for k in ("ml", "ats", "tot"):
        if f"{k}_y" not in df:
            continue
        d = df.dropna(subset=[f"{k}_y", f"{k}_p_model", f"{k}_p_mkt"])
        lm = np.array([_ll(p, y) for p, y in zip(d[f"{k}_p_model"], d[f"{k}_y"])])
        lk = np.array([_ll(p, y) for p, y in zip(d[f"{k}_p_mkt"], d[f"{k}_y"])])
        diff = lm - lk
        boots = sorted(np.mean([diff[rng.randrange(len(diff))] for _ in range(len(diff))]) for _ in range(n_boot)) \
            if len(diff) else [float("nan")]
        out[k] = {"n": int(len(d)), "logloss_model": float(lm.mean()), "logloss_market": float(lk.mean()),
                  "logloss_coinflip": math.log(2),
                  "brier_model": float(((d[f"{k}_p_model"] - d[f"{k}_y"]) ** 2).mean()),
                  "brier_market": float(((d[f"{k}_p_mkt"] - d[f"{k}_y"]) ** 2).mean()),
                  "diff_model_minus_market": float(diff.mean()),
                  "diff_ci95": [float(boots[int(0.025 * n_boot)]), float(boots[int(0.975 * n_boot) - 1])],
                  "model_better_than_market": bool(boots[int(0.975 * n_boot) - 1] < 0)}
    if "ats_push_p" in df:
        whole = df.dropna(subset=["ats_push_p"])
        out["ats_push_pred"] = float(whole.ats_push_p.sum())
        out["ats_push_actual"] = int(whole.get("ats_push", pd.Series(dtype=float)).fillna(0).sum())
    return out


def calibration(df, k, bins=10):
    d = df.dropna(subset=[f"{k}_y", f"{k}_p_model"])
    edges = np.linspace(0, 1, bins + 1)
    rows = []
    for lo, hi in zip(edges, edges[1:]):
        s = d[(d[f"{k}_p_model"] >= lo) & (d[f"{k}_p_model"] < hi)]
        if len(s):
            rows.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(len(s)), "pred": float(s[f"{k}_p_model"].mean()),
                         "obs": float(s[f"{k}_y"].mean())})
    return rows


def run(features, first, last):
    per, models = [], {}
    for variant in ("independent", "anchored"):
        for s in range(first, last + 1):
            sm, df = score_season(features, s, variant)
            models[(variant, s)] = sm
            per.append(df)
    allr = pd.concat(per, ignore_index=True)
    report = {"seasons": f"{first}-{last}", "variants": {}}
    for variant in ("independent", "anchored"):
        v = allr[allr.variant == variant]
        hist = v[v.season < 2026]
        report["variants"][variant] = {
            "pooled_out_of_sample_through_2025": summarize(hist),
            "season_2026_to_date": summarize(v[v.season == 2026]) if (v.season == 2026).any() else None,
            "by_season": {int(s): summarize(x, n_boot=300) for s, x in hist.groupby("season")},
            "calibration_ml": calibration(hist, "ml"), "calibration_ats": calibration(hist, "ats"),
            "last_model_coefficients": {"margin": models[(variant, last)].margin.coefficients(),
                                        "total": models[(variant, last)].total.coefficients(),
                                        "margin_sigma": models[(variant, last)].margin.sigma,
                                        "total_sigma": models[(variant, last)].total.sigma}}
    return report, allr


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", default="data/derived/features.parquet")
    ap.add_argument("--first", type=int, default=2012)
    ap.add_argument("--last", type=int, default=2026)
    ap.add_argument("--out", default="docs_v4/engine_c_eval.json")
    a = ap.parse_args(argv)
    f = pd.read_parquet(a.features)
    report, rows = run(f, a.first, a.last)
    rows.to_parquet("data/derived/engine_c_oos_predictions.parquet", index=False)
    with open(a.out, "w") as fh:
        json.dump(report, fh, indent=2, default=float)
    for v, r in report["variants"].items():
        p = r["pooled_out_of_sample_through_2025"]
        print(v, {k: (round(p[k]["logloss_model"], 4), round(p[k]["logloss_market"], 4), p[k]["model_better_than_market"])
                  for k in ("ml", "ats", "tot")},
              "rmse margin", round(p["margin_rmse_model"], 2), "vs mkt", round(p["margin_rmse_market"], 2))


if __name__ == "__main__":
    main()
