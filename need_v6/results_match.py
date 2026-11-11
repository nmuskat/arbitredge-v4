"""
need_v6/results_match.py -- final-score matching with an FCS-aware CFB fallback.

The platform matcher (data_v4.results.match) resolves CFB names through the FBS school list, so games against FCS
opponents (e.g. "Idaho Vandals", "Arkansas Pine Bluff Golden Lions") were left unmatched. Fallback, only for games
the platform matcher did not resolve: normalise names; a results-row school matches an Odds API team when it is the
LONGEST school name that is a prefix of the team name ("Idaho State Bengals" -> "Idaho State", not "Idaho"); both
teams must match, kickoff within 36 h, and exactly one candidate row. Anything else stays unmatched (never guessed).
"""

import re

from data_v4 import results as R
from sportsbook_v4.util import to_epoch


def norm(s):
    s = s.lower().replace("&", " and ").replace("-", " ").replace(".", " ").replace("'", "")
    s = re.sub(r"\bst\b", "state", s)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


# Odds API spelling -> CollegeFootballData spelling (normalised), only where the two sources differ
ALIASES = {"albany": "ualbany", "houston baptist": "houston christian", "liu": "long island university",
           "southern mississippi": "southern miss", "umass": "massachusetts", "citadel": "the citadel",
           "southeastern louisiana": "se louisiana"}


def _school_for(team, schools):
    t = norm(team)
    for k, v in ALIASES.items():
        if (t == k or t.startswith(k + " ")) and v in schools:
            return v
    best = None
    for s in schools:
        if t == s or t.startswith(s + " "):
            if best is None or len(s) > len(best):
                best = s
    return best


def match(games, docs, cfb_matcher=None):
    out = R.match(games, docs, cfb_matcher)
    latest = {}
    for d in docs:
        if d["sport"] != "cfb":
            continue
        for r in d["rows"]:
            latest[r["source_id"]] = dict(r, source=d["source"], fetched_at=d["fetched_at"])
    rows = list(latest.values())
    schools = {norm(r["home"]) for r in rows} | {norm(r["away"]) for r in rows}
    for g in games:
        if g["sport"] != "cfb" or (g["sport"], g["event_id"]) in out:
            continue
        hs, as_ = _school_for(g["home"], schools), _school_for(g["away"], schools)
        if not hs or not as_:
            continue
        kick = to_epoch(g["kickoff"])
        cands = [r for r in rows if r.get("final") and norm(r["home"]) == hs and norm(r["away"]) == as_
                 and r.get("start") and abs(to_epoch(r["start"]) - kick) <= 36 * 3600]
        swapped = False
        if not cands:          # neutral sites: the two sources may list home/away the other way round
            cands = [r for r in rows if r.get("final") and norm(r["home"]) == as_ and norm(r["away"]) == hs
                     and r.get("start") and abs(to_epoch(r["start"]) - kick) <= 36 * 3600]
            swapped = True
        if len(cands) == 1:
            r = cands[0]
            h, a = (r["away_score"], r["home_score"]) if swapped else (r["home_score"], r["away_score"])
            out[("cfb", g["event_id"])] = {"home_score": h, "away_score": a,
                                           "source_id": r["source_id"], "source": r["source"],
                                           "fetched_at": r["fetched_at"], "corrected": False,
                                           "matched_by": "fcs_fallback" + ("_swapped" if swapped else "")}
    return out
