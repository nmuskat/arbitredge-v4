"""
sims_v4/features.py -- strictly point-in-time team features.

Games are processed in calendar order (US Eastern date). Features for every
game on date D are computed from team state built ONLY from games on dates
< D; state is updated after the whole date is processed. So a 4:25 pm game
never sees the 1 pm results of the same day (conservative).

State per team
  * margin-aware Elo (FiveThirtyEight-style MOV multiplier), regressed 1/3
    toward the mean between seasons;
  * EWMA (half-life HL games) of offensive EPA/play, defensive EPA/play
    allowed, success rates, points for/against and offensive plays, shrunk
    halfway to league average between seasons.

Flagged inputs
  * qb_change_* uses the ACTUAL starting QB listed in games.csv. Starters are
    usually known before kickoff, but not always: an assumption, reported.
  * Weather (temp/wind) is NOT used: the file records observed weather, which
    would be look-ahead without a forecast archive.
"""

import math
from collections import defaultdict

import numpy as np
import pandas as pd

ELO_MEAN, ELO_K, ELO_HFA = 1505.0, 20.0, 48.0
HL = 6.0
EW_KEYS = ("off_epa", "def_epa", "off_sr", "def_sr", "pf", "pa", "off_plays")
LEAGUE_DEFAULT = {"off_epa": 0.0, "def_epa": 0.0, "off_sr": 0.43, "def_sr": 0.43, "pf": 22.0, "pa": 22.0,
                  "off_plays": 62.0}


def _mov_mult(mov, elo_diff_winner):
    return math.log(abs(mov) + 1.0) * 2.2 / (elo_diff_winner * 0.001 + 2.2)


class TeamState:
    def __init__(self):
        self.elo = ELO_MEAN
        self.ew = dict(LEAGUE_DEFAULT)
        self.n = 0              # games observed (all seasons)
        self.n_season = 0
        self.season = None
        self.last_qb = None

    def new_season(self, season):
        if self.season is not None and season != self.season:
            self.elo = ELO_MEAN + (self.elo - ELO_MEAN) * (2.0 / 3.0)
            for k in EW_KEYS:
                self.ew[k] = 0.5 * self.ew[k] + 0.5 * LEAGUE_DEFAULT[k]
            self.n_season = 0
        self.season = season


def build_features(games, team_epa):
    """games: output of nfl_data.load_games; team_epa: output of team_game_epa.
    Returns games with feature columns (one row per game, same order)."""
    epa = team_epa.set_index(["game_id", "team"])
    teams = defaultdict(TeamState)
    alpha = 1.0 - 0.5 ** (1.0 / HL)
    rows = []
    for date, day in games.groupby("game_date", sort=True):
        for _, g in day.iterrows():
            h, a = teams[g.home_team], teams[g.away_team]
            h.new_season(g.season)
            a.new_season(g.season)
            hq, aq = g.get("home_qb_id"), g.get("away_qb_id")
            rows.append({
                "game_id": g.game_id,
                "elo_home": h.elo, "elo_away": a.elo, "elo_diff": h.elo - a.elo,
                **{f"h_{k}": h.ew[k] for k in EW_KEYS}, **{f"a_{k}": a.ew[k] for k in EW_KEYS},
                "h_n": h.n, "a_n": a.n, "h_n_season": h.n_season, "a_n_season": a.n_season,
                "qb_change_home": int(isinstance(hq, str) and h.last_qb is not None and hq != h.last_qb),
                "qb_change_away": int(isinstance(aq, str) and a.last_qb is not None and aq != a.last_qb),
            })
        # ---- update state AFTER the whole date (no same-day leakage)
        for _, g in day.iterrows():
            if not g.played:
                continue
            h, a = teams[g.home_team], teams[g.away_team]
            mov = g.home_score - g.away_score
            hfa = 0.0 if g.neutral else ELO_HFA
            diff = h.elo + hfa - a.elo
            exp_h = 1.0 / (1.0 + 10 ** (-diff / 400.0))
            res = 1.0 if mov > 0 else (0.5 if mov == 0 else 0.0)
            mult = _mov_mult(mov, diff if mov > 0 else -diff) if mov != 0 else 1.0
            shift = ELO_K * mult * (res - exp_h)
            h.elo += shift
            a.elo -= shift
            for team, st, pf, pa, qb in ((g.home_team, h, g.home_score, g.away_score, g.get("home_qb_id")),
                                          (g.away_team, a, g.away_score, g.home_score, g.get("away_qb_id"))):
                obs = {"pf": pf, "pa": pa}
                if (g.game_id, team) in epa.index:
                    r = epa.loc[(g.game_id, team)]
                    obs.update(off_epa=r.off_epa, def_epa=r.def_epa, off_sr=r.off_sr, def_sr=r.def_sr,
                               off_plays=r.off_plays)
                for k, v in obs.items():
                    if v is not None and not (isinstance(v, float) and math.isnan(v)):
                        st.ew[k] = (1 - alpha) * st.ew[k] + alpha * float(v)
                st.n += 1
                st.n_season += 1
                if isinstance(qb, str):
                    st.last_qb = qb
    f = pd.DataFrame(rows)
    out = games.merge(f, on="game_id", how="left", validate="one_to_one")
    out["home_field"] = 1 - out["neutral"]
    out["rest_diff"] = (out["home_rest"] - out["away_rest"]).clip(-7, 7).fillna(0)
    out["epa_net_diff"] = (out.h_off_epa - out.h_def_epa) - (out.a_off_epa - out.a_def_epa)
    out["sr_net_diff"] = (out.h_off_sr - out.h_def_sr) - (out.a_off_sr - out.a_def_sr)
    out["pts_net_diff"] = (out.h_pf - out.h_pa) - (out.a_pf - out.a_pa)
    out["tot_pts_env"] = out.h_pf + out.h_pa + out.a_pf + out.a_pa
    out["tot_epa_env"] = out.h_off_epa + out.a_def_epa + out.a_off_epa + out.h_def_epa
    out["tot_plays_env"] = out.h_off_plays + out.a_off_plays
    out["qb_change_diff"] = out.qb_change_home - out.qb_change_away
    return out
