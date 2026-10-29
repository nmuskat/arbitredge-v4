"""
sportsbook_v4/popularity.py -- transparent team-popularity index for an EXPERIMENTAL bettor-model version.

Popularity is NOT a betting percentage. The index only scales two existing, bounded behaviours:
  * game choice:  a recreational bettor's weight for a game x (pop_home x pop_away) ** (0.5 x popularity_weight)
  * side choice:  logit term popularity_weight x (log pop_home - log pop_away)
so a popular team draws somewhat more attention and lean. Nothing is assigned a target share of wagers.

Inputs (one CSV per sport and season; every value cited):
    team, indicator, value, source, url, retrieved_at
Indicators (each log-transformed, then standardised across the sport's teams):
    tv_viewers_avg     national average viewers per game (Nielsen, as published)
    search_interest    Google Trends interest, same query window for every team (manual export)
    valuation_usd      franchise valuation (NFL: Forbes / Sportico)
    attendance_avg     average home attendance (official / ESPN)
    social_followers   total followers across major platforms (team accounts)
An indicator is used ONLY if it covers at least COVERAGE_MIN of the sport's teams: a partial list would
silently make every missing team "average". Missing values within a used indicator take the mean (0 z).

index_z = weighted mean of the used indicators' z-scores (weights below, renormalised over used ones)
multiplier = exp(SCALE x index_z), clipped to [MIN_MULT, MAX_MULT], then rescaled to geometric mean 1.0
so the overall amount of betting is unchanged; only its distribution across teams moves.
All weights and the scale are UNVALIDATED assumptions, to be calibrated against observed splits.
"""

import csv
import math

INDICATORS = {"tv_viewers_avg": 0.35, "search_interest": 0.25, "valuation_usd": 0.15,
              "attendance_avg": 0.10, "social_followers": 0.15}
COVERAGE_MIN = 0.90
SCALE = 0.25            # one standard deviation of popularity = x1.28 multiplier (assumption)
MIN_MULT, MAX_MULT = 0.6, 1.8


def read_inputs(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if not (r.get("team") and r.get("indicator") and r.get("value")):
                continue
            if not (r.get("source") and r.get("url") and r.get("retrieved_at")):
                raise ValueError(f"{r['team']}/{r['indicator']}: every value needs source, url and retrieved_at")
            if r["indicator"] not in INDICATORS:
                raise ValueError(f"unknown indicator {r['indicator']}")
            v = float(r["value"])
            if v <= 0:
                raise ValueError(f"{r['team']}/{r['indicator']}: value must be positive")
            rows.append({"team": r["team"], "indicator": r["indicator"], "value": v})
    return rows


def index(rows, teams):
    """{team: multiplier}, plus a report of which indicators were used and why others were not."""
    teams = sorted(set(teams))
    used, report = {}, {}
    for ind, w in INDICATORS.items():
        vals = {r["team"]: math.log(r["value"]) for r in rows if r["indicator"] == ind and r["team"] in teams}
        cov = len(vals) / len(teams) if teams else 0.0
        if cov < COVERAGE_MIN or len(vals) < 2:
            report[ind] = {"used": False, "coverage": round(cov, 3), "reason": f"coverage below {COVERAGE_MIN:.0%}"}
            continue
        m = sum(vals.values()) / len(vals)
        sd = math.sqrt(sum((x - m) ** 2 for x in vals.values()) / (len(vals) - 1))
        if sd == 0:
            report[ind] = {"used": False, "coverage": round(cov, 3), "reason": "no variation"}
            continue
        used[ind] = ({t: (vals[t] - m) / sd for t in vals}, w)
        report[ind] = {"used": True, "coverage": round(cov, 3), "weight": w}
    if not used:
        return {t: 1.0 for t in teams}, report
    wsum = sum(w for _z, w in used.values())
    raw = {}
    for t in teams:
        z = sum(zs.get(t, 0.0) * w for zs, w in used.values()) / wsum
        raw[t] = min(MAX_MULT, max(MIN_MULT, math.exp(SCALE * z)))
    gm = math.exp(sum(math.log(v) for v in raw.values()) / len(raw))
    return {t: round(v / gm, 6) for t, v in raw.items()}, report
