"""
sportsbook_v4/live_book.py -- Engine A (synthetic sportsbook) on REAL collected quotes.

Everything produced here is MODELED: simulated customers with unvalidated
behaviour assumptions, betting into the real prices V4 collected. Nothing
here is an observed betting split, and the output says so on every row.

  * Separate persistent populations: 10,000 NFL bettors and 10,000 CFB
    bettors (population.py -- bettor k is the same bettor in every build).
  * NFL: every game with collected quotes. CFB: only games with an AP
    Top-25 team in the poll applicable at kickoff (data_v4.rankings).
    Without an applicable poll the CFB book is UNAVAILABLE -- never guessed.
  * Offered price = the representative real quote, no older than
    LIVE_MAX_AGE at the bettor's decision time. If no fresh quote exists the
    book is closed and nobody bets (a collection gap never invents prices).
  * Tickets % comes from wager COUNTS, handle % from STAKES. They differ
    whenever stake sizes differ by side (e.g. large recreational or sharp
    stakes on one side), never by construction.
  * Need comes from the book's liability (settlement.book_need_all), NOT
    from fading the public.
  * Sensitivity bands: the same build under named parameter variants
    (common random numbers), so a split that swings across variants is
    flagged as assumption-driven.
  * Auditable: per run the population hash, params hash, engine version,
    run id and the ledger's wagers hash are reported.
"""

import re
import sqlite3
import time

from market_v4 import asof
from sportsbook_v4 import ENGINE_VERSION, params as P
from sportsbook_v4.engine import GameSpec, SlateSpec, advance, create_run
from sportsbook_v4.facts import NoFacts
from sportsbook_v4.ledger_read import fetch_wagers
from sportsbook_v4.market import MarketV4Source
from sportsbook_v4.population import ensure_population
from sportsbook_v4.schema import ensure_schema
from sportsbook_v4.settlement import book_need_all
from sportsbook_v4.util import json_hash

LABEL = "MODELED"
LIVE_MAX_AGE = 6 * 3600          # collection gaps far from kickoff are several hours (cron_plan)
SPORT_KEYS = {"nfl": "americanfootball_nfl", "cfb": "americanfootball_ncaaf"}
MAX_SCORE = {"nfl": 55, "cfb": 80}
HORIZON_DAYS = 8
LOW_SAMPLE_TICKETS = 50          # fewer modeled tickets than this in a market: shown, flagged LOW sample
WAGER_MARKET = {"spread": "spreads", "total": "totals", "moneyline": "h2h"}
SPLIT_SIDES = {"spread": ("home", "away"), "total": ("over", "under"), "moneyline": ("home", "away")}

# The Odds API's NFL team names (fixed universe -> a stable, persistent population).
NFL_TEAMS = (
    "Arizona Cardinals", "Atlanta Falcons", "Baltimore Ravens", "Buffalo Bills", "Carolina Panthers",
    "Chicago Bears", "Cincinnati Bengals", "Cleveland Browns", "Dallas Cowboys", "Denver Broncos",
    "Detroit Lions", "Green Bay Packers", "Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars",
    "Kansas City Chiefs", "Las Vegas Raiders", "Los Angeles Chargers", "Los Angeles Rams", "Miami Dolphins",
    "Minnesota Vikings", "New England Patriots", "New Orleans Saints", "New York Giants", "New York Jets",
    "Philadelphia Eagles", "Pittsburgh Steelers", "San Francisco 49ers", "Seattle Seahawks",
    "Tampa Bay Buccaneers", "Tennessee Titans", "Washington Commanders",
)


def _scaled_fav(factor):
    return {a: {"fav_pref": (s["fav_pref"][0] * factor, s["fav_pref"][1],
                             min(s["fav_pref"][2], s["fav_pref"][0] * factor),
                             max(s["fav_pref"][3], s["fav_pref"][0] * factor))}
            for a, s in P.ARCHETYPES.items()}


def _shares(**changes):
    out, delta = {}, 0.0
    for arch, share in changes.items():
        delta += share - P.ARCHETYPES[arch]["share"]
        out[arch] = {"share": share}
    out["REC_CASUAL"] = {"share": round(P.ARCHETYPES["REC_CASUAL"]["share"] - delta, 10)}
    return out


# name -> kwargs for params.variant (evaluated lazily: they read the baseline params)
SENSITIVITY_VARIANTS = {
    "fav_pref_half": lambda: {"archetype_overrides": _scaled_fav(0.5)},
    "fav_pref_x1.5": lambda: {"archetype_overrides": _scaled_fav(1.5)},
    "sharp_share_6pct": lambda: {"archetype_overrides": _shares(SHARP=0.06)},
    "timing_all_steady": lambda: {"all_archetypes": {"timing": "steady"}},
}


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def team_id(sport, name):
    return f"{sport}_{slug(name)}"


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def candidate_games(live_conn, sport, T, horizon_days=HORIZON_DAYS):
    """Games with collected quotes kicking off after T and within the horizon."""
    rows = live_conn.execute(
        "SELECT event_id, MIN(commence), home, away, MIN(fetched_at) FROM mkt_quote WHERE sport=? "
        "GROUP BY event_id HAVING MIN(commence) > ? AND MIN(commence) <= ? ORDER BY MIN(commence), home",
        (SPORT_KEYS[sport], T, T + horizon_days * 86400)).fetchall()
    return [{"event_id": e, "kickoff": int(k), "home": h, "away": a, "first_quote": int(fq)}
            for e, k, h, a, fq in rows if fq <= T]


def select_games(live_conn, sport, T, rankings_snapshots=None):
    """(games, coverage). CFB keeps only Top-25 games under the poll applicable at kickoff."""
    games = candidate_games(live_conn, sport, T)
    if sport == "nfl":
        return games, {"status": "OK", "rule": "all NFL games with collected quotes", "candidates": len(games),
                       "included": len(games)}
    from data_v4 import rankings
    if not rankings_snapshots:
        return [], {"status": "UNAVAILABLE", "rule": "AP Top-25 games only",
                    "reason": "no AP rankings snapshot available (CollegeFootballData key not connected)",
                    "candidates": len(games), "included": 0}
    keep, excluded = [], {"unranked": 0, "rank_unavailable": 0, "rank_pending": 0}
    for g in games:
        st = rankings.top25_status(g["home"], g["away"], g["kickoff"], rankings_snapshots)
        g["rankings"] = st
        if st["status"] == "PENDING":
            excluded["rank_pending"] += 1          # next week's poll not out yet: joins once it is
        elif st["status"] != "OK":
            excluded["rank_unavailable"] += 1
        elif st["top25"]:
            keep.append(g)
        else:
            excluded["unranked"] += 1
    return keep, {"status": "OK" if keep or games else "NO_GAMES", "rule": "AP Top-25 games only",
                  "candidates": len(games), "included": len(keep), "excluded": excluded}


def universe(sport, rankings_snapshots=None, games=()):
    """Team universe of the persistent population. NFL: the fixed 32. CFB: FBS schools from the
    latest rankings snapshot (stable within a season)."""
    if sport == "nfl":
        return sorted(team_id("nfl", n) for n in NFL_TEAMS)
    snaps = [s for s in rankings_snapshots or [] if s.get("teams")]
    if snaps:
        latest = max(snaps, key=lambda s: s["fetched_at"])
        return sorted({team_id("cfb", t["school"]) for t in latest["teams"]})
    return sorted({team_id("cfb", n) for g in games for n in (g["home"], g["away"])})


def _team_key(sport, name, matcher):
    if sport == "cfb" and matcher is not None:
        school = matcher.school_for(name)
        if school:
            return team_id("cfb", school)
    return team_id(sport, name)


def splits(wagers):
    """MODELED splits per market and side: tickets % from wager counts, handle % from stakes."""
    out = {}
    for m, sides in SPLIT_SIDES.items():
        ws = [w for w in wagers if w["market"] == m]
        n, h = len(ws), sum(w["stake"] for w in ws)
        if n == 0:
            out[m] = {"status": "NO_WAGERS", "label": LABEL}
            continue
        row = {"status": "OK", "label": LABEL, "tickets": n, "handle": round(h, 2), "sides": {},
               "sample": "LOW" if n < LOW_SAMPLE_TICKETS else "OK"}
        for s in sides:
            sw = [w for w in ws if w["side"] == s]
            st = sum(w["stake"] for w in sw)
            row["sides"][s] = {"tickets": len(sw), "handle": round(st, 2),
                               "ticket_pct": round(100 * len(sw) / n, 1), "handle_pct": round(100 * st / h, 1),
                               "avg_stake": round(st / len(sw), 2) if sw else None}
        a = sides[0]
        row["handle_minus_tickets_pct"] = round(row["sides"][a]["handle_pct"] - row["sides"][a]["ticket_pct"], 1)
        row["of_side"] = a
        out[m] = row
    return out


def _reference(live_conn, gid, T):
    """Consensus spread and total as of the game's LAST OBSERVATION at or before T (labelled with
    that time). Between collection runs the build time can be hours after it, when a fresh-only
    view would be empty."""
    r = live_conn.execute("SELECT MAX(fetched_at) FROM mkt_quote WHERE event_id=? AND fetched_at<=?",
                          (gid, T)).fetchone()
    at = r[0] if r and r[0] is not None else T
    sp = asof.market_state(live_conn, gid, "spreads", at)
    to = asof.market_state(live_conn, gid, "totals", at)
    return (sp.get("line") if sp.get("status") == "OK" else None,
            to.get("line") if to.get("status") == "OK" else None, at)


def _need_summary(need):
    """Compact Need: which side's result the book's P&L is best on, per market (liability fact)."""
    out = {}
    sp = need.get("spread") or {}
    if sp.get("status") in ("DIRECTIONAL", "TIE"):
        out["spread"] = {k: sp.get(k) for k in ("financial_need_side", "handle", "tickets", "pnl_swing",
                                                 "pnl_if_home_covers_all", "pnl_if_away_covers_all",
                                                 "worst_case_pnl", "worst_case_margins", "best_case_pnl", "status") if k in sp}
    for m in ("total", "moneyline"):
        x = need.get(m) or {}
        if x.get("status") in ("DIRECTIONAL", "TIE"):
            out[m] = {k: v for k, v in x.items() if not isinstance(v, (list, dict))}
    j = need.get("joint") or {}
    if j.get("status") == "OK":
        out["joint"] = {"best_case": j["best_case"], "worst_case": j["worst_case"], "regions": j.get("regions")}
    return out


def run_book(book_conn, live_conn, sport, T, games, team_universe, bettors=10_000, matcher=None,
             slate_tag="live"):
    """Advance one synthetic book to T on the real quotes; return the run's audit facts."""
    if not games:
        return None
    pid = ensure_population(book_conn, sport, team_universe, bettor_count=bettors)
    ws = min(g["first_quote"] for g in games) // 3600 * 3600
    we = max(g["kickoff"] for g in games)
    slate = SlateSpec(sport, f"{slate_tag}_{sport}_{_iso(ws)[:13]}", _iso(ws), _iso(we),
                      tuple(GameSpec(g["event_id"], _team_key(sport, g["home"], matcher),
                                     _team_key(sport, g["away"], matcher), _iso(g["kickoff"])) for g in games))
    market = MarketV4Source(live_conn, max_age_seconds=LIVE_MAX_AGE,
                            source_id=f"live_quotes:{sport}:max_age={LIVE_MAX_AGE}")
    run_id = create_run(book_conn, pid, slate, market)
    until = min(T, we)
    adv = advance(book_conn, run_id, _iso(until), market, NoFacts(), data_complete_through=_iso(T))
    wagers = fetch_wagers(book_conn, run_id, market=None)
    pop_hash = book_conn.execute("SELECT population_hash FROM v4_population WHERE population_id=?",
                                 (pid,)).fetchone()[0]
    return {"run_id": run_id, "population_id": pid, "population_hash": pop_hash, "params_hash": P.params_hash(),
            "engine_version": ENGINE_VERSION, "window": [_iso(ws), _iso(we)], "advanced_to": _iso(until),
            "bettors": bettors, "arrivals": adv.get("arrivals"), "wagers": len(wagers),
            "ledger_hash": json_hash([[w["wager_id"], w["market"], w["side"], w["accepted_line"],
                                       w["accepted_price"], w["stake"], w["accepted_at"]] for w in wagers]),
            "market_source": market.source_id, "_wagers": wagers}


def _by_game(wagers):
    out = {}
    for w in wagers:
        out.setdefault(w["canonical_game_id"], []).append(w)
    return out


def build(book_conn, live_conn, T, rankings_snapshots=None, bettors=10_000, variants=SENSITIVITY_VARIANTS):
    """Both sports. Returns {sport: {status, coverage, audit, games:[...], sensitivity}}."""
    from data_v4 import rankings
    out = {}
    for sport in ("nfl", "cfb"):
        games, coverage = select_games(live_conn, sport, T, rankings_snapshots)
        block = {"label": LABEL, "coverage": coverage, "games": [], "audit": None, "sensitivity": None,
                 "note": "Simulated customers with unvalidated behaviour assumptions betting into real collected "
                         "prices. Not observed betting splits."}
        if coverage["status"] == "UNAVAILABLE":
            block["status"] = "UNAVAILABLE"
            out[sport] = block
            continue
        if not games:
            block["status"] = "NO_GAMES"
            out[sport] = block
            continue
        matcher = None
        if sport == "cfb":
            latest = max((s for s in rankings_snapshots if s.get("teams")), key=lambda s: s["fetched_at"], default=None)
            matcher = rankings.TeamMatcher(latest["teams"]) if latest else None
        uni = universe(sport, rankings_snapshots, games)
        base = run_book(book_conn, live_conn, sport, T, games, uni, bettors, matcher)
        per_game = _by_game(base.pop("_wagers"))
        block["audit"] = base
        block["status"] = "OK" if base["wagers"] else "NO_WAGERS"

        # sensitivity: identical arrivals (common random numbers), different behaviour assumptions
        var_splits = {}
        for name, kw in (variants or {}).items():
            vconn = sqlite3.connect(":memory:")       # a population is bound to its params hash
            ensure_schema(vconn)
            with P.variant(name, **kw()):
                r = run_book(vconn, live_conn, sport, T, games, uni, bettors, matcher)
            vconn.close()
            for gid, ws in _by_game(r.pop("_wagers")).items():
                var_splits.setdefault(gid, {})[name] = splits(ws)
        block["sensitivity"] = {"variants": sorted(variants or {}),
                                "method": "min/max of each split across the baseline and every variant"}

        for g in games:
            ws = per_game.get(g["event_id"], [])
            ref_spread, ref_total, ref_at = _reference(live_conn, g["event_id"], T)
            sp = splits(ws)
            bands = _bands(sp, var_splits.get(g["event_id"], {}))
            need = book_need_all(ws, ref_home_spread=ref_spread, ref_total=ref_total,
                                 max_score=MAX_SCORE[sport]) if ws else None
            block["games"].append({
                "event_id": g["event_id"], "home": g["home"], "away": g["away"], "kickoff": _iso(g["kickoff"]),
                "rankings": g.get("rankings"), "reference": {"home_spread": ref_spread, "total": ref_total,
                                                            "as_of": _iso(ref_at),
                                                            "basis": "observed consensus at last collection"},
                "splits": sp, "bands": bands, "need": _need_summary(need) if need else None,
                "label": LABEL})
        out[sport] = block
    return out


def _bands(base, variants):
    """{market: {side: {ticket_pct: [lo, hi], handle_pct: [lo, hi]}}} across baseline + variants."""
    out = {}
    for m, row in base.items():
        if row.get("status") != "OK":
            continue
        out[m] = {}
        for s, v in row["sides"].items():
            tp, hp = [v["ticket_pct"]], [v["handle_pct"]]
            for vs in variants.values():
                r = vs.get(m) or {}
                if r.get("status") == "OK":
                    tp.append(r["sides"][s]["ticket_pct"])
                    hp.append(r["sides"][s]["handle_pct"])
            out[m][s] = {"ticket_pct": [min(tp), max(tp)], "handle_pct": [min(hp), max(hp)], "n_variants": len(tp) - 1}
    return out
