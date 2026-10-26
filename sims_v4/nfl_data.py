"""
sims_v4/nfl_data.py -- load nflverse games and team-game EPA.

The games.csv line columns are an approximate consensus near close of
unspecified book/time: usable as a BENCHMARK, never as an executable price.
"""

import os
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")
FRANCHISE = {"OAK": "LV", "SD": "LAC", "STL": "LA"}   # relocations -> current franchise code


def franchise(code):
    return FRANCHISE.get(code, code)


def load_games(path):
    g = pd.read_csv(path)
    g = g[g["game_type"].isin(["REG", "WC", "DIV", "CON", "SB"])].copy()
    for c in ("home_team", "away_team"):
        g[c] = g[c].map(franchise)
    t = pd.to_datetime(g["gameday"] + " " + g["gametime"].fillna("13:00"), format="%Y-%m-%d %H:%M")
    g["kickoff_utc"] = t.dt.tz_localize(ET, ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert("UTC")
    g["game_date"] = pd.to_datetime(g["gameday"]).dt.date
    g["neutral"] = (g["location"] == "Neutral").astype(int)
    g["played"] = g["result"].notna()
    # nfldata: spread_line > 0 means HOME favoured; i.e. market expected home margin = +spread_line
    g["mkt_margin"] = g["spread_line"]
    g["indoor"] = g["roof"].isin(["dome", "closed"]).astype(int)
    g = g.sort_values(["kickoff_utc", "game_id"]).reset_index(drop=True)
    return g


def team_game_epa(pbp_paths, cache=None):
    """Per (game_id, team): offensive plays, EPA/play, success rate (run+pass plays only),
    and the same allowed on defence."""
    if cache and os.path.exists(cache):
        return pd.read_parquet(cache)
    cols = ["game_id", "posteam", "defteam", "play_type", "epa", "success", "pass", "rush"]
    frames = []
    for p in pbp_paths:
        d = pd.read_parquet(p, columns=cols)
        d = d[d["play_type"].isin(["pass", "run"]) & d["epa"].notna()]
        frames.append(d)
    d = pd.concat(frames, ignore_index=True)
    d["posteam"] = d["posteam"].map(franchise)
    d["defteam"] = d["defteam"].map(franchise)
    off = d.groupby(["game_id", "posteam"]).agg(off_plays=("epa", "size"), off_epa=("epa", "mean"),
                                                 off_sr=("success", "mean"),
                                                 off_pass_rate=("pass", "mean")).reset_index().rename(columns={"posteam": "team"})
    de = d.groupby(["game_id", "defteam"]).agg(def_epa=("epa", "mean"), def_sr=("success", "mean")
                                                ).reset_index().rename(columns={"defteam": "team"})
    out = off.merge(de, on=["game_id", "team"], how="outer")
    if cache:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        out.to_parquet(cache, index=False)
    return out
