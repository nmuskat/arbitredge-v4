"""desk_v1: point-in-time ratings, independence from NEED/SIMS, probability/EV maths, w rule, data-quality rule."""
import ast
import glob
import os

import numpy as np
import pandas as pd
import pytest

from desk_v1 import fit as F
from desk_v1 import model as M
from desk_v1.spec import SPEC, SPEC_HASH


def test_desk_never_imports_need_sims_or_sportsbook():
    banned = ("sportsbook_v4", "need_v6", "intel_v7", "lab_v4", "sims_v5", "arbsims")
    for p in glob.glob(os.path.join(os.path.dirname(M.__file__), "*.py")):
        tree = ast.parse(open(p).read())
        mods = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module] + \
               [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        bad = [m for m in mods if m.split(".")[0] in banned and not (m == "need_v6" or m.startswith("need_v6.normalize"))]
        # the only allowed foreign import is the tested normaliser (pure maths, no NEED data)
        assert not bad, (p, bad)
        src = open(p).read().lower()
        assert "financial_need" not in src and "need_side" not in src


def test_ratings_recover_known_effects():
    rng = np.random.default_rng(1)
    teams = [f"T{i}" for i in range(8)]
    o = {t: rng.normal(0, .1) for t in teams}
    d = {t: rng.normal(0, .1) for t in teams}
    rows = []
    for _ in range(30000):
        a, b = rng.choice(teams, 2, replace=False)
        ho = rng.random() < .5
        rows.append((a, b, ho, o[a] - d[b] + 0.04 * ho + rng.normal(0, .3)))
    r = M.fit_ratings(pd.DataFrame(rows, columns=["off", "def", "home_off", "epa"]), teams, 1.0)
    assert abs(r["h"] - 0.04) < 0.01
    assert np.corrcoef([o[t] for t in teams], [r["o"][t] for t in teams])[0, 1] > 0.97


def test_ridge_prior_shrinks_to_prior():
    teams = ["A", "B"]
    prior = {"o": {"A": 0.5, "B": -0.5}, "d": {"A": 0.0, "B": 0.0}}
    r = M.fit_ratings(pd.DataFrame(columns=["off", "def", "home_off", "epa"]), teams, 100.0, prior)
    assert abs(r["o"]["A"] - 0.5) < 1e-6 and abs(r["o"]["B"] + 0.5) < 1e-6      # no data -> prior


def test_point_in_time_features_ignore_current_and_later_weeks(tmp_path, monkeypatch):
    """Features for week k must not change when plays of weeks >= k change."""
    def fake_plays(sport, raw, season):
        rng = np.random.default_rng(season)
        rows = []
        for wk in range(1, 6):
            for gnum in range(4):
                h, a = f"T{gnum}", f"T{gnum + 4}"
                for _ in range(60):
                    off, de = (h, a) if rng.random() < .5 else (a, h)
                    e = rng.normal(0, .3) + (5.0 if (season == 2025 and wk >= 3) else 0.0)
                    rows.append((f"{season}_{wk}_{gnum}", season, wk, off, de, off == h, e, False, False, None))
        return pd.DataFrame(rows, columns=["game_id", "season", "week", "off", "def", "home_off", "epa", "garbage",
                                           "dropback", "passer_id"])

    def fake_games(sport, raw, season):
        rows = [(f"{season}_{wk}_{g}", season, wk, f"T{g}", f"T{g + 4}", 20, 17, False) for wk in range(1, 6) for g in range(4)]
        return pd.DataFrame(rows, columns=["game_id", "season", "week", "home", "away", "home_pts", "away_pts", "neutral"])
    monkeypatch.setattr(F.DT, "plays", fake_plays)
    monkeypatch.setattr(F.DT, "games", fake_games)
    _, feats, _, _ = F.season_features("nfl", None, 2025, lam_rho=[(50, 0.0)])
    f = feats[(50, 0.0)]
    # week-3 and later plays are shifted by +5 EPA: week 3 features must not see them; week 4 must
    assert abs(f["2025_3_0"][1]) < 1.0 and f["2025_4_0"][1] > 1.0


def test_ev_and_push_maths():
    nz = M.normalizer("nfl", "spread", round(M.nfl_base_sigma("spread"), 2))
    e, pw, pp = M.ev(nz, 3.0, "spread", "home", -3.0, -110)
    assert pp > 0.05                                     # NFL key number 3 carries push mass
    assert abs(e - (pw * (100 / 110) - (1 - pw - pp))) < 1e-12
    e2, pw2, pp2 = M.ev(nz, 3.0, "spread", "home", -2.5, -110)
    assert pp2 == 0.0 and pw2 > pw


def test_w_zero_rule():
    rng = np.random.default_rng(3)
    noise = [(i, rng.normal(0, 10), rng.normal(0, 2)) for i in range(300)]       # model signal unrelated to outcome
    w, lo, hi = F.w_with_ci(noise, "t")
    assert lo <= 0 <= hi
    signal = [(i, 0.8 * x + rng.normal(0, 1), x) for i, x in ((i, rng.normal(0, 5)) for i in range(300))]
    w2, lo2, hi2 = F.w_with_ci(signal, "t")
    assert lo2 > 0 and abs(w2 - 0.8) < 0.1


def test_spec_hash_stable():
    import hashlib
    import json
    assert SPEC_HASH == hashlib.sha256(json.dumps(SPEC, sort_keys=True).encode()).hexdigest()
    assert SPEC["min_ev"] == 0.02 and SPEC["min_gap_points"] == {"spread": 1.0, "total": 1.5}
