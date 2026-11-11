"""
desk_v1/data.py -- plays and games from the free play-by-play files (nflverse / cfbfastR), with the frozen filters.

plays: game_id, season, week, off, def, home_off (False at neutral sites), epa, garbage, dropback, passer_id
games: game_id, season, week, home, away, home_pts, away_pts, neutral, kickoff_utc, proxy market fields (CLOSING
       lines; labelled proxy -- never a decision-time quote)
"""
import os

import numpy as np
import pandas as pd

from desk_v1.spec import SPEC


def nfl_plays(raw, season):
    cols = ["game_id", "week", "posteam", "defteam", "home_team", "play_type", "epa", "wp", "qtr", "qb_kneel",
            "qb_spike", "two_point_conv_result", "qb_dropback", "passer_player_id"]
    p = pd.read_parquet(os.path.join(raw, f"nfl_pbp_{season}.parquet"), columns=cols)
    g = nfl_games(raw, season)[["game_id", "neutral"]]
    p = p[p.play_type.isin(SPEC["nfl_play_types"]) & p.epa.notna() & (p.qtr <= 4)
          & (p.qb_kneel.fillna(0) == 0) & (p.qb_spike.fillna(0) == 0) & p.two_point_conv_result.isna()
          & p.posteam.notna() & p.defteam.notna()]
    p = p.merge(g, on="game_id", how="left")
    lo, hi = SPEC["garbage_wp"]
    return pd.DataFrame({"game_id": p.game_id.values, "season": season, "week": p.week.astype(int).values,
                         "off": p.posteam.values, "def": p.defteam.values,
                         "home_off": ((p.posteam.astype(str) == p.home_team.astype(str)) & ~p.neutral.fillna(False).astype(bool)).values,
                         "epa": p.epa.astype(float).values,
                         "garbage": ((p.wp < lo) | (p.wp > hi)).fillna(False).values,
                         "dropback": (p.qb_dropback.fillna(0) == 1).values,
                         "passer_id": p.passer_player_id.values})


def nfl_games(raw, season):
    g = pd.read_csv(os.path.join(raw, "nfl_games.csv"))
    g = g[g.season == season].copy()
    ko = pd.to_datetime(g.gameday + " " + g.gametime).dt.tz_localize("America/New_York", ambiguous="NaT",
                                                                       nonexistent="NaT").dt.tz_convert("UTC")
    return pd.DataFrame({"game_id": g.game_id.values, "season": season, "week": g.week.astype(int).values,
                         "home": g.home_team.values, "away": g.away_team.values,
                         "home_pts": g.home_score.values, "away_pts": g.away_score.values,
                         "neutral": (g.location == "Neutral").values, "kickoff_utc": ko.values,
                         # PROXY (closing) market, nflverse consensus: spread_line > 0 = home favoured
                         "close_home_line": (-g.spread_line).values, "close_home_price": g.home_spread_odds.values,
                         "close_away_price": g.away_spread_odds.values, "close_total": g.total_line.values,
                         "close_over_price": g.over_odds.values, "close_under_price": g.under_odds.values})


def cfb_plays(raw, season):
    cols = ["game_id", "week", "pos_team", "def_pos_team", "home", "play_type", "EPA", "wp_before", "period",
            "neutral_site"]
    p = pd.read_parquet(os.path.join(raw, f"cfb_pbp_{season}.parquet"), columns=cols)
    p = p[p.play_type.isin(SPEC["cfb_play_types"]) & p.EPA.notna() & (p.period <= 4) & p.pos_team.notna()
          & p.def_pos_team.notna()]
    lo, hi = SPEC["garbage_wp"]
    return pd.DataFrame({"game_id": p.game_id.astype(str).values, "season": season, "week": p.week.astype(int).values,
                         "off": p.pos_team.values, "def": p.def_pos_team.values,
                         "home_off": ((p.pos_team.astype(str) == p.home.astype(str)) & ~p.neutral_site.fillna(False).astype(bool)).values,
                         "epa": p.EPA.astype(float).values,
                         "garbage": ((p.wp_before < lo) | (p.wp_before > hi)).fillna(False).values,
                         "dropback": np.zeros(len(p), dtype=bool), "passer_id": None})


def cfb_games(raw, season):
    cols = ["game_id", "week", "home", "away", "pos_team", "pos_team_score", "def_pos_team_score", "game_play_number",
            "neutral_site", "start_date", "spread", "over_under", "period"]
    p = pd.read_parquet(os.path.join(raw, f"cfb_pbp_{season}.parquet"), columns=cols)
    p["game_id"] = p.game_id.astype(str)
    # final score = the scores carried on the LAST play of the game (score columns flip on kickoffs/scoring plays,
    # so a per-column maximum is wrong -- found and fixed 2026-11-11 before any freeze)
    last = p.sort_values(["game_id", "game_play_number"]).groupby("game_id").tail(1).set_index("game_id")
    is_home = (last.pos_team.astype(str) == last.home.astype(str)).to_numpy()
    last["home_score"] = np.where(is_home, last.pos_team_score, last.def_pos_team_score)
    last["away_score"] = np.where(is_home, last.def_pos_team_score, last.pos_team_score)
    first = p.groupby("game_id").agg(week=("week", "first"), home=("home", "first"), away=("away", "first"),
                                     neutral=("neutral_site", "first"), start=("start_date", "first"),
                                     spread=("spread", "first"), ou=("over_under", "first"))
    agg = first.join(last[["home_score", "away_score", "period"]]).reset_index().rename(
        columns={"home_score": "home_pts", "away_score": "away_pts"})
    # DATA QUALITY (fixed rule, all seasons): a CFB game's score is usable only if its play-by-play reaches the 4th
    # period and does not end level (college games cannot tie); otherwise the score is treated as MISSING.
    bad = (agg.period < 4) | (agg.home_pts == agg.away_pts)
    agg.loc[bad, ["home_pts", "away_pts"]] = np.nan
    return pd.DataFrame({"game_id": agg.game_id.values, "season": season, "week": agg.week.astype(int).values,
                         "home": agg.home.values, "away": agg.away.values, "home_pts": agg.home_pts.values,
                         "away_pts": agg.away_pts.values, "neutral": agg.neutral.fillna(False).astype(bool).values,
                         "kickoff_utc": pd.to_datetime(agg.start, utc=True).values,
                         # PROXY (closing) market: cfbfastR spread is the HOME line (negative = home favoured)
                         "close_home_line": agg.spread.values, "close_home_price": -110.0, "close_away_price": -110.0,
                         "close_total": agg.ou.values, "close_over_price": -110.0, "close_under_price": -110.0})


def plays(sport, raw, season):
    return nfl_plays(raw, season) if sport == "nfl" else cfb_plays(raw, season)


def games(sport, raw, season):
    return nfl_games(raw, season) if sport == "nfl" else cfb_games(raw, season)


def cfb_schedule(raw, season):
    """Scheduled CFB games (including games not yet played) from the free cfbfastR schedule file; scores NaN when
    not completed. Used only to identify an upcoming game's week, teams and neutral site."""
    sc = pd.read_parquet(os.path.join(raw, f"cfb_schedules_{season}.parquet"))
    return pd.DataFrame({"game_id": sc.game_id.astype(str).values, "season": season, "week": sc.week.astype(int).values,
                         "home": sc.home_team.values, "away": sc.away_team.values,
                         "home_pts": sc.home_points.values, "away_pts": sc.away_points.values,
                         "neutral": sc.neutral_site.fillna(False).astype(bool).values,
                         "kickoff_utc": pd.to_datetime(sc.start_date, utc=True).values})
