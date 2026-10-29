"""
sportsbook_v4/params_calib.py -- EXPERIMENTAL bettor-model candidates (NOT ACTIVE).

A candidate is a named overlay on the active parameters, applied only inside params.variant() for offline
evaluation (tools_v4/model_effect.py, tools_v4/split_calibration.py). The live sportsbook never imports this
module. Activating a candidate would mean copying it into a NEW PARAMS_VERSION used only by weekly runs that
open after the owner approves; existing runs, wagers, Need decisions, cohort memberships and results are
never touched.

Candidate m5_draft1 (every value an assumption awaiting calibration against observed splits):
  * favourite pull depends on spread size (fav_spread_curve): weaker near pick'em, stronger for
    touchdown-plus favourites. Literature is mixed (Paul & Weinbach: big favourites draw more bets;
    Shank 2019: preference is non-linear and weaker at very large spreads), so the curve flattens past 10.
  * high-stakes recreational bankrolls lowered (median $40,000 -> $15,000) to reduce the extreme handle
    concentration measured in the live ledger (3.7% of tickets = 60% of NFL spread handle). The right
    level is exactly what observed ticket% vs handle% gaps can calibrate.
  * team popularity from sportsbook_v4/popularity.py inputs (neutral until the input tables are filled).
  * national-TV / primetime classification v2 already applies to new weeks (live_book.classify).
  Unchanged on purpose: home preference (mean 0), kickoff-relative arrival timing (owner kept as is).
"""

import os

from sportsbook_v4 import popularity

REC = ("REC_CASUAL", "NARRATIVE_CHASER", "HIGH_STAKES_REC", "TEAM_LOYAL", "UNDERDOG_CONTRARIAN")
FAV_SPREAD_CURVE = [(0.0, 0.6), (2.5, 0.9), (3.5, 1.0), (7.0, 1.15), (10.0, 1.25), (17.0, 1.25)]
INPUTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_v4", "popularity")


def team_popularity(sport, team_ids, season=2026):
    path = os.path.join(INPUTS, f"inputs_{sport}_{season}.csv")
    rows = popularity.read_inputs(path) if os.path.exists(path) else []
    return popularity.index(rows, team_ids)


def m5_draft1(sport=None, team_ids=()):
    """Keyword arguments for params.variant(): the candidate as an overlay."""
    over = {a: {"fav_spread_curve": FAV_SPREAD_CURVE} for a in REC}
    over["HIGH_STAKES_REC"]["bankroll_median"] = 15000.0
    pop, _report = team_popularity(sport, team_ids) if sport and team_ids else ({}, {})
    return {"archetype_overrides": over, "team_popularity": {t: v for t, v in pop.items() if v != 1.0}}


CANDIDATES = {"m5_draft1": m5_draft1}
