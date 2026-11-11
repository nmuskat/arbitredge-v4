"""
desk_v1/fit.py -- forward-only 2025 procedure and the final 2025 fit (frozen before any 2026 evaluation).

  python -m desk_v1.fit --raw DIR --out DIR [--sports nfl,cfb]

2025 market = CLOSING line (proxy; labelled) because no 2025 decision-time quotes exist in our data.
For every 2025 week k >= SPEC first_forward_week: all choices use weeks [2, k-1] only; week k is predicted.
The final fit applies the identical procedure to all 2025 weeks and writes desk_v1_params.json + sha256.
"""
import argparse
import hashlib
import json
import math
import os
import random

import numpy as np
import pandas as pd

from desk_v1 import VERSION
from desk_v1 import data as DT
from desk_v1 import model as M
from desk_v1.spec import SPEC, SPEC_HASH

MARKETS = ("spread", "total")


def season_features(sport, raw, season, prev_season=None, lam_rho=None):
    """Forward features of every completed game of `season` for every (lambda, rho) in the grid (or one pair)."""
    pl = DT.plays(sport, raw, season)
    prev = DT.plays(sport, raw, prev_season or season - 1)
    gm = DT.games(sport, raw, season)
    gm = gm[gm.home_pts.notna() & gm.away_pts.notna()].copy()
    teams = sorted(set(pl.off) | set(pl["def"]) | set(prev.off) | set(prev["def"]))
    clean, prev_clean = pl[~pl.garbage], prev[~prev.garbage]
    grid = lam_rho or [(lam, rho) for lam in SPEC["lambda_grid"] for rho in SPEC["rho_grid"]]
    prev_fit = {lam: M.fit_ratings(prev_clean, teams, lam) for lam in {l for l, _ in grid}}
    _, prev_league = M.pace_table(prev)
    feats = {c: {} for c in grid}
    games_before = {}
    for wk in sorted(gm.week.unique()):
        before = pl[pl.week < wk]
        pace, league = M.pace_table(before)
        league = league or prev_league
        for g in gm[gm.week == wk].itertuples():
            games_before[g.game_id] = min(pace.get(g.home, (0, 0))[0], pace.get(g.away, (0, 0))[0])
        for lam, rho in grid:
            r = M.fit_ratings(clean[clean.week < wk], teams, lam, M.scaled(prev_fit[lam], rho))
            for g in gm[gm.week == wk].itertuples():
                feats[(lam, rho)][g.game_id] = M.team_features(r, pace, league, SPEC["pace_shrink_to_league"],
                                                               g.home, g.away, bool(g.neutral))
    return gm, feats, games_before, prev_fit


def proxy_market(sport, gm):
    """Closing-line PROXY market means (no-vig, push-aware) for every game."""
    out = {}
    for g in gm.itertuples():
        res = {}
        for m in MARKETS:
            nz = M.normalizer(sport, m, round(M.nfl_base_sigma(m) if sport == "nfl" else 16.0, 2))
            if m == "spread":
                if pd.isna(g.close_home_line):
                    continue
                mu = M.market_mu(nz, m, float(g.close_home_line), float(g.close_home_price), -float(g.close_home_line),
                                 float(g.close_away_price))
            else:
                if pd.isna(g.close_total):
                    continue
                mu = M.market_mu(nz, m, float(g.close_total), float(g.close_over_price), float(g.close_total),
                                 float(g.close_under_price))
            if mu is not None:
                res[m] = mu
        out[g.game_id] = res
    return out


def outcome(g, m):
    return (g.home_pts - g.away_pts) if m == "spread" else (g.home_pts + g.away_pts)


def select_combo(train, feats, m):
    """Inner criterion: MSE of game margin (total) after a linear map of the raw football signal, per combo."""
    best = None
    for c, f in feats.items():
        rows = [(f[g.game_id], outcome(g, m)) for g in train.itertuples() if g.game_id in f]
        if len(rows) < 10:
            continue
        x = np.array([(a[0] * a[1] - a[2] * a[3]) if m == "spread" else (a[0] * a[1] + a[2] * a[3]) for a, _ in rows])
        y = np.array([b for _, b in rows], dtype=float)
        X = np.vstack([np.ones_like(x), x]).T
        sol, *_ = np.linalg.lstsq(X, y, rcond=None)
        mse = float(np.mean((y - X @ sol) ** 2))
        if best is None or mse < best[0] - 1e-12:
            best = (mse, c)
    return best[1] if best else None


def fit_fold(sport, train, feats, mkt, m, combo, rng_seed):
    """Points model, w (with bootstrap zero rule), sigma and k on the training games."""
    f = feats[combo]
    rows = []
    for g in train.itertuples():
        if g.game_id in f:
            a = f[g.game_id]
            rows += [(a[0] * a[1], g.home_pts), (a[2] * a[3], g.away_pts)]
    alpha, beta = M.fit_points(rows)
    resid = []
    for g in train.itertuples():
        if g.game_id in f and m in mkt.get(g.game_id, {}):
            fb = M.football_lines(f[g.game_id], alpha, beta)[0 if m == "spread" else 1]
            resid.append((g.game_id, outcome(g, m) - mkt[g.game_id][m], fb - mkt[g.game_id][m]))
    w_hat, lo, hi = w_with_ci(resid, rng_seed)
    w = 0.0 if lo <= 0 else min(1.0, w_hat)
    if sport == "nfl":
        sigma0 = M.nfl_base_sigma(m)
    else:
        sigma0 = math.sqrt(sum(r[1] ** 2 for r in resid) / len(resid)) if resid else 16.0
    k = fit_k(sport, m, train, f, mkt, alpha, beta, w, sigma0)
    return {"combo": list(combo), "alpha": alpha, "beta": beta, "w": w, "w_hat": w_hat, "w_ci": [lo, hi],
            "sigma0": sigma0, "k": k, "sigma": round(sigma0 * k, 2), "n_train": len(resid)}


def w_with_ci(resid, seed):
    if len(resid) < 10:
        return 0.0, 0.0, 0.0
    y = np.array([r[1] for r in resid])
    x = np.array([r[2] for r in resid])
    w = float((x @ y) / max(1e-9, x @ x))
    rng = random.Random(seed)
    n = len(resid)
    bs = []
    for _ in range(SPEC["w_bootstrap"]):
        ix = [rng.randrange(n) for _ in range(n)]
        xx, yy = x[ix], y[ix]
        bs.append(float((xx @ yy) / max(1e-9, xx @ xx)))
    bs.sort()
    return w, bs[int(0.025 * len(bs))], bs[int(0.975 * len(bs)) - 1]


def fit_k(sport, m, train, f, mkt, alpha, beta, w, sigma0):
    """k by maximum likelihood of cover/over outcomes at the proxy line (pushes excluded)."""
    best = None
    a_side = "home" if m == "spread" else "over"
    for k in SPEC["k_grid"]:
        nz = M.normalizer(sport, m, round(sigma0 * k, 2))
        ll, n = 0.0, 0
        for g in train.itertuples():
            if g.game_id not in f or m not in mkt.get(g.game_id, {}):
                continue
            line = g.close_home_line if m == "spread" else g.close_total
            if pd.isna(line):
                continue
            fb = M.football_lines(f[g.game_id], alpha, beta)[0 if m == "spread" else 1]
            mu = mkt[g.game_id][m] + w * (fb - mkt[g.game_id][m])
            pw, pp = M.side_probs(nz, mu, m, a_side, float(line))
            y = outcome(g, m)
            covered = (y + line > 0) if m == "spread" else (y > line)
            push = (y + line == 0) if m == "spread" else (y == line)
            if push or pp >= 1:
                continue
            p = min(max(pw / (1 - pp), 1e-6), 1 - 1e-6)
            ll += math.log(p if covered else 1 - p)
            n += 1
        if n and (best is None or ll > best[0]):
            best = (ll, k)
    return best[1] if best else 1.0


def decide(sport, m, g, mu_mkt, mu_desk, nz, games_before):
    """Proxy-market decision for a 2025 game (closing line and price; labelled not executable)."""
    if games_before < SPEC["min_prior_games"]:
        return {"decision": "PASS", "reason": "fewer than 3 prior games"}
    gap = mu_desk - mu_mkt
    if abs(gap) < SPEC["min_gap_points"][m]:
        return {"decision": "PASS", "reason": "gap below threshold", "gap": gap}
    if m == "spread":
        side = "home" if gap > 0 else "away"
        point = float(g.close_home_line) if side == "home" else -float(g.close_home_line)
        price = float(g.close_home_price) if side == "home" else float(g.close_away_price)
    else:
        side = "over" if gap > 0 else "under"
        point = float(g.close_total)
        price = float(g.close_over_price) if side == "over" else float(g.close_under_price)
    e, pw, pp = M.ev(nz, mu_desk, m, side, point, price)
    if e < SPEC["min_ev"]:
        return {"decision": "PASS", "reason": "EV below threshold", "gap": gap, "ev": e}
    return {"decision": "LEAN", "side": side, "point": point, "price": price, "ev": e, "p_win": pw, "p_push": pp,
            "gap": gap}


def forward_2025(sport, raw):
    gm, feats, nb, _ = season_features(sport, raw, 2025)
    mkt = proxy_market(sport, gm)
    weeks = sorted(gm.week.unique())
    preds = []
    for k in [w for w in weeks if w >= SPEC["first_forward_week_2025"]]:
        train = gm[(gm.week >= 2) & (gm.week < k)]
        test = gm[gm.week == k]
        for m in MARKETS:
            combo = select_combo(train, feats, m)
            if combo is None:
                continue
            fp = fit_fold(sport, train, feats, mkt, m, combo, rng_seed=f"{sport}{m}{k}")
            nz = M.normalizer(sport, m, fp["sigma"])
            for g in test.itertuples():
                if g.game_id not in feats[combo] or m not in mkt.get(g.game_id, {}):
                    continue
                fb = M.football_lines(feats[combo][g.game_id], fp["alpha"], fp["beta"])[0 if m == "spread" else 1]
                mu_m = mkt[g.game_id][m]
                mu_d = mu_m + fp["w"] * (fb - mu_m)
                line = g.close_home_line if m == "spread" else g.close_total
                a_side = "home" if m == "spread" else "over"
                pw_d, pp_d = M.side_probs(nz, mu_d, m, a_side, float(line))
                pw_m, pp_m = M.side_probs(nz, mu_m, m, a_side, float(line))
                preds.append({"sport": sport, "market": m, "week": int(k), "game_id": g.game_id, "y": float(outcome(g, m)),
                              "line": float(line), "mu_mkt": mu_m, "mu_fb": fb, "mu_desk": mu_d, "w": fp["w"],
                              "p_desk": pw_d / max(1e-9, 1 - pp_d), "p_mkt": pw_m / max(1e-9, 1 - pp_m),
                              **decide(sport, m, g, mu_m, mu_d, nz, nb.get(g.game_id, 0))})
    return preds


def final_fit(sport, raw):
    gm, feats, nb, prev_fit = season_features(sport, raw, 2025)
    mkt = proxy_market(sport, gm)
    train = gm[gm.week >= 2]
    out = {}
    for m in MARKETS:
        combo = select_combo(train, feats, m)
        out[m] = fit_fold(sport, train, feats, mkt, m, combo, rng_seed=f"{sport}{m}final")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sports", default="nfl,cfb")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    params = {"version": VERSION, "spec_hash": SPEC_HASH, "trained_on": "2025 (closing-line PROXY market)",
              "sports": {}}
    allp = []
    for sport in a.sports.split(","):
        allp += forward_2025(sport, a.raw)
        params["sports"][sport] = final_fit(sport, a.raw)
        print(sport, json.dumps(params["sports"][sport]), flush=True)
    json.dump(allp, open(os.path.join(a.out, "forward_2025.json"), "w"), default=float)
    body = json.dumps(params, sort_keys=True, default=float)
    params["params_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    json.dump(params, open(os.path.join(a.out, "desk_v1_params.json"), "w"), sort_keys=True, indent=1, default=float)
    print("params sha256", params["params_sha256"])


if __name__ == "__main__":
    main()
