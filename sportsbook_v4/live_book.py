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
  * PERSISTENT LEDGER: one fixed run per sport per betting week (Tuesday
    12:00 UTC to the next Tuesday). Its game list is fixed when the week's
    book opens; games listed later are reported, not added. Every advance is
    exported once as an immutable file (sportsbook_v4.persist, release
    v4-book) and reloaded by later builds, so committed wagers are never
    re-drawn. Each build re-simulates the week from scratch and requires the
    identical wager set before anything new is exported (replay check).
  * The book is advanced only to the sport's LAST COLLECTED OBSERVATION
    (all market data up to then exists), never to the build time.
  * Auditable: per run the population hash, params hash, engine version,
    run id, ledger file count and the ledger's wagers hash are reported.
"""

import calendar
import math
import re
import sqlite3
import time

from market_v4 import asof
from sportsbook_v4 import ENGINE_VERSION, params as P, persist
from sportsbook_v4.engine import GameSpec, RunMismatch, SlateSpec, advance, create_run, load_run
from sportsbook_v4.facts import NoFacts
from sportsbook_v4.ledger_read import fetch_wagers
from sportsbook_v4 import need_tracker
from sportsbook_v4.market import MarketV4Source, profit_on_win, representative_offer
from sportsbook_v4.population import ensure_population
from sportsbook_v4.schema import ensure_schema
from sportsbook_v4.settlement import book_need_all
from sportsbook_v4.util import from_epoch, json_hash, to_epoch

LABEL = "MODELED"
LIVE_MAX_AGE = 6 * 3600          # collection gaps far from kickoff are several hours (cron_plan)
SPORT_KEYS = {"nfl": "americanfootball_nfl", "cfb": "americanfootball_ncaaf"}
MAX_SCORE = {"nfl": 55, "cfb": 80}
DATA_SAFETY_S = 15 * 60         # collect jobs time out after 5 min, so any fetch older than this is uploaded
WEEK_START_WDAY = 1             # Tuesday (time.gmtime: Monday = 0)
WEEK_START_HOUR_UTC = 12        # after Monday Night Football has finished
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


def week_window(t):
    """(start, end) epoch of the betting week containing t: Tuesday 12:00 UTC to the next Tuesday."""
    g = time.gmtime(t)
    day0 = calendar.timegm((g.tm_year, g.tm_mon, g.tm_mday, 0, 0, 0))
    start = day0 - ((g.tm_wday - WEEK_START_WDAY) % 7) * 86400 + WEEK_START_HOUR_UTC * 3600
    if start > t:
        start -= 7 * 86400
    return start, start + 7 * 86400


def complete_through(L, T):
    """Market data is complete up to here: every fetch made before T - DATA_SAFETY_S has been uploaded
    (collection runs are serialised and time out after 5 minutes), and nothing is newer than L before
    the next fetch. The book never advances past it."""
    return max(L, T - DATA_SAFETY_S)


def last_observation(live_conn, sport, T):
    """Time of the sport's latest collection run with quotes, at or before T (None if never)."""
    r = live_conn.execute("SELECT MAX(fetched_at) FROM mkt_fetch WHERE sport=? AND n_quotes>0 AND fetched_at<=?",
                          (SPORT_KEYS[sport], T)).fetchone()
    return None if not r or r[0] is None else r[0]


def week_games(live_conn, sport, ws, we, L):
    """Games kicking off inside the week whose first quote was collected by L."""
    rows = live_conn.execute(
        "SELECT event_id, MIN(commence), home, away, MIN(fetched_at) FROM mkt_quote WHERE sport=? AND fetched_at<=? "
        "GROUP BY event_id HAVING MIN(commence) >= ? AND MIN(commence) < ? ORDER BY MIN(commence), home",
        (SPORT_KEYS[sport], L, ws, we)).fetchall()
    return [{"event_id": e, "kickoff": int(k), "home": h, "away": a, "first_quote": int(fq)}
            for e, k, h, a, fq in rows]


def select_week(live_conn, sport, ws, we, L, rankings_snapshots=None):
    """(games, coverage) that open the week's book. CFB: AP Top-25 games under the poll applicable at
    kickoff, using only polls fetched by L. If a poll that should already be out has not been fetched,
    the book waits for it; games whose poll is not released yet are excluded and reported."""
    games = week_games(live_conn, sport, ws, we, L)
    if sport == "nfl":
        return games, {"status": "OK", "rule": "all NFL games of the week with collected quotes",
                       "candidates": len(games), "included": len(games)}
    from data_v4 import rankings
    snaps = [x for x in rankings_snapshots or [] if x["fetched_at"] <= L]
    if not snaps:
        return [], {"status": "UNAVAILABLE", "rule": "AP Top-25 games only",
                    "reason": "no AP rankings snapshot available (CollegeFootballData key not connected)",
                    "candidates": len(games), "included": 0}
    keep, excluded, missed = [], {"unranked": 0, "rank_unavailable": 0, "rank_pending": 0}, 0
    for g in games:
        st = rankings.top25_status(g["home"], g["away"], g["kickoff"], snaps)
        g["rankings"] = st
        if st["status"] == "PENDING":
            # poll released before L but not fetched yet -> wait; poll not released yet -> excluded
            if g["kickoff"] < rankings.next_release(L):
                missed += 1
            excluded["rank_pending"] += 1
        elif st["status"] != "OK":
            excluded["rank_unavailable"] += 1
        elif st["top25"]:
            keep.append(g)
        else:
            excluded["unranked"] += 1
    cov = {"status": "OK" if games else "NO_GAMES", "rule": "AP Top-25 games only", "candidates": len(games),
           "included": len(keep), "excluded": excluded}
    if missed:
        cov.update(status="WAITING_FOR_POLL", included=0,
                   reason="this week's AP poll was released but has not been fetched yet; "
                          "the week's book opens once it is")
        return [], cov
    return keep, cov


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
    """MODELED splits per market and side: tickets % from wager counts, handle % from stakes, potential
    payout and the book's P&L if that side wins, all from each wager's accepted price."""
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
            # each wager at ITS OWN accepted price: stake back plus winnings
            pay = math.fsum(w["stake"] + profit_on_win(w["stake"], w["accepted_price"]) for w in sw)
            row["sides"][s] = {"tickets": len(sw), "handle": round(st, 2),
                               "ticket_pct": round(100 * len(sw) / n, 1), "handle_pct": round(100 * st / h, 1),
                               "avg_stake": round(st / len(sw), 2) if sw else None,
                               "potential_payout": round(pay, 2),
                               # book keeps every stake, pays this side's winners (pushes not modelled here)
                               "book_pnl_if_side_wins": round(h - pay, 2)}
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


def _market(live_conn, sport):
    return MarketV4Source(live_conn, max_age_seconds=LIVE_MAX_AGE,
                          source_id=f"live_quotes:{sport}:max_age={LIVE_MAX_AGE}")


def model_tag():
    """Engine + parameter identity of a weekly run. A model change opens a NEW run for the week; runs
    from earlier model versions stay in the ledger, immutable, and are reported as superseded."""
    return f"{ENGINE_VERSION.rsplit('.', 1)[-1]}_{P.params_hash()[:8]}"


def slate_id(sport, ws):
    return f"week_{sport}_{time.strftime('%Y%m%d', time.gmtime(ws))}_{model_tag()}"


def _current_model(book_conn, run_id):
    ev, ph = book_conn.execute("SELECT engine_version, params_hash FROM v4_run WHERE run_id=?", (run_id,)).fetchone()
    return ev == ENGINE_VERSION and ph == P.params_hash()


STANDALONE_GAP_S = 90 * 60     # no other game of the sport kicks off within 90 min: a standalone window


def standalone_ids(all_week_games):
    """Games alone in their kickoff window (TNF/SNF/MNF, lone night games), from the full week's
    collected schedule of the sport. National-TV assignments are not in the data: not inferred."""
    ks = [(g["event_id"], g["kickoff"]) for g in all_week_games]
    return {e for e, k in ks if not any(o != e and abs(k2 - k) < STANDALONE_GAP_S for o, k2 in ks)}


# Game-window classification v2, for weekly runs that OPEN on or after this time only (runs already open
# keep the flags they were opened with, so no accepted wager or replay changes):
#   NFL  national_tv = standalone (TNF / SNF / MNF / international / any lone window: exclusive national
#        broadcasts). Regional Sunday games stay regional.
#   CFB  primetime = standalone OR kickoff 7:00 pm - 11:59 pm ET (Saturday-night games). National-TV
#        assignments are not in the data, so national_tv stays False rather than being guessed.
CLASSIFICATION_V2_FROM = "2026-11-03T12:00:00Z"


def classify(sport, kickoff, standalone, ws):
    """(standalone/primetime flag, national_tv flag) used to open a run's GameSpec."""
    if ws < to_epoch(CLASSIFICATION_V2_FROM):
        return standalone, False
    if sport == "nfl":
        return standalone, standalone
    import datetime
    from zoneinfo import ZoneInfo
    et_hour = datetime.datetime.fromtimestamp(kickoff, ZoneInfo("America/New_York")).hour
    return standalone or et_hour >= 19, False


def week_open_bankrolls(book_conn, pid, sport, ws, settlements):
    """V5: every customer's bankroll at the week's open = opening bankroll + settled P&L of their wagers in
    this engine release's EARLIER weeks (runs whose window ended by ws), from the authoritative settlement
    rows. No settled history -> the opening bankroll. Never below zero. Frozen into the new run."""
    from sportsbook_v4 import bettor_settlement as BS
    from sportsbook_v4.population import load_population
    prior = {r[0] for r in book_conn.execute(
        "SELECT run_id FROM v4_run WHERE sport=? AND engine_version=? AND population_id=? AND window_end<=?",
        (sport, ENGINE_VERSION, pid, from_epoch(ws)))}
    pnl = {}
    for s_ in BS.latest(settlements or []).values():
        if s_["run_id"] in prior:
            pnl[s_["bettor_index"]] = pnl.get(s_["bettor_index"], 0) + s_["pnl_cents"]
    out = []
    for b in load_population(book_conn, pid)["bettors"]:
        out.append([b["bettor_index"], max(0.0, round(b["bankroll"] + pnl.get(b["bettor_index"], 0) / 100.0, 2))])
    return out


def open_run(book_conn, live_conn, sport, ws, we, games, team_universe, bettors=10_000, matcher=None,
             standalone=frozenset(), settlements=None):
    pid = ensure_population(book_conn, sport, team_universe, bettor_count=bettors)
    # times in the engine's canonical format: the run id is hashed from these strings and a replay
    # rebuilds the slate from the stored run row, so both must be byte-identical
    slate = SlateSpec(sport, slate_id(sport, ws), from_epoch(ws), from_epoch(we),
                      tuple(GameSpec(g["event_id"], _team_key(sport, g["home"], matcher),
                                     _team_key(sport, g["away"], matcher), from_epoch(g["kickoff"]),
                                     *classify(sport, g["kickoff"], g["event_id"] in standalone, ws))
                            for g in games))
    snap = week_open_bankrolls(book_conn, pid, sport, ws, settlements) if ENGINE_VERSION.startswith("sportsbook_v5") else None
    return create_run(book_conn, pid, slate, _market(live_conn, sport), bankroll_snapshot=snap)


def _population_universe(book_conn, run_id):
    pid, uni, n = book_conn.execute(
        "SELECT p.population_id, p.team_universe, p.bettor_count FROM v4_run r JOIN v4_population p "
        "ON p.population_id = r.population_id WHERE r.run_id=?", (run_id,)).fetchone()
    import json
    return json.loads(uni), n


def replay(book_conn, live_conn, run_id, until_iso, data_through_iso, variant=None):
    """Re-simulate a run from scratch in memory to until_iso. Returns (mem_conn, mem_run_id)."""
    run = load_run(book_conn, run_id)
    uni, n = _population_universe(book_conn, run_id)
    mem = sqlite3.connect(":memory:")
    ensure_schema(mem)
    ctx = P.variant(variant[0], **variant[1]()) if variant else None
    if ctx:
        ctx.__enter__()
    try:
        pid = ensure_population(mem, run["slate"].sport, uni, bettor_count=n)
        rid = create_run(mem, pid, run["slate"], _market(live_conn, run["slate"].sport),
                         bankroll_snapshot=persist.snapshot_rows(book_conn, run_id) or None)
        if to_epoch(until_iso) > to_epoch(run["slate"].window_start):
            advance(mem, rid, until_iso, _market(live_conn, run["slate"].sport), NoFacts(),
                    data_complete_through=data_through_iso)
    finally:
        if ctx:
            ctx.__exit__(None, None, None)
    return mem, rid


def _watermark_iso(book_conn, run_id):
    r = book_conn.execute("SELECT interval_end FROM v4_interval WHERE run_id=? ORDER BY seq DESC LIMIT 1",
                          (run_id,)).fetchone()
    return r[0] if r else load_run(book_conn, run_id)["slate"].window_start


REPLAY_VERIFY_EVERY_S = 12 * 3600   # full from-scratch verification at most this often per run (every build
                                     # still hash-verifies every ledger file, its sequence and its wagers)


def advance_and_export(book_conn, live_conn, run_id, L, book_out, verify=True):
    """Advance a run to min(L, window end); verify against a from-scratch replay (when `verify`); export the
    new interval. Returns (status, new_wagers)."""
    run = load_run(book_conn, run_id)
    sport = run["slate"].sport
    until = _iso(min(L, to_epoch(run["slate"].window_end)))
    try:
        adv = advance(book_conn, run_id, until, _market(live_conn, sport), NoFacts(), data_complete_through=_iso(L))
    except RunMismatch as e:
        return f"FROZEN: {e}", 0
    wm = _watermark_iso(book_conn, run_id)
    if verify:
        mem, rid = replay(book_conn, live_conn, run_id, wm, _iso(L))
        same = rid == run_id and persist.wager_set(mem, rid) == persist.wager_set(book_conn, run_id)
        mem.close()
        if not same:
            return "INTEGRITY_FAILED: stored ledger differs from a from-scratch replay", 0
    if adv.get("status") == "advanced":
        if book_out:
            persist.export_interval(book_conn, run_id, adv["seq"], book_out)
        return "OK", adv["accepted"]
    return "OK", 0


def _names(live_conn, gid):
    r = live_conn.execute("SELECT home, away FROM mkt_quote WHERE event_id=? LIMIT 1", (gid,)).fetchone()
    return r if r else (None, None)


def _by_game(wagers):
    out = {}
    for w in wagers:
        out.setdefault(w["canonical_game_id"], []).append(w)
    return out


def _tracker_games(book_conn, live_conn, sport):
    """Every game the current model's weekly runs carried (the Need tracker's universe)."""
    out = []
    for rid, in book_conn.execute("SELECT run_id FROM v4_run WHERE sport=? AND engine_version=? AND params_hash=?",
                                  (sport, ENGINE_VERSION, P.params_hash())).fetchall():
        for g in load_run(book_conn, rid)["slate"].games:
            home, away = _names(live_conn, g.canonical_game_id)
            out.append({"sport": sport, "event_id": g.canonical_game_id, "home": home, "away": away,
                        "kickoff": _iso(to_epoch(g.commence_time))})
    return out


SENSITIVITY_REFRESH_S = 3 * 3600   # variant replays (most of a build's time) at most this often per weekly run


def build(book_conn, live_conn, T, rankings_snapshots=None, bettors=10_000, variants=SENSITIVITY_VARIANTS,
          book_in=None, book_out=None, sensitivity_cache=None, settlements=None):
    """Both sports. Loads the persisted ledger (book_in), advances each sport's weekly run to its last
    observation, exports new intervals (book_out). Returns {sport: {status, coverage, audit, games, ...}}."""
    from data_v4 import rankings
    note = ("Simulated customers with unvalidated behaviour assumptions betting into real collected prices. "
            "Not observed betting splits.")
    try:
        loaded = persist.load_dir(book_conn, book_in)
    except persist.LedgerIntegrityError as e:
        return {sp: {"label": LABEL, "status": "INTEGRITY_FAILED", "reason": str(e), "coverage": {}, "games": [],
                     "audit": None, "sensitivity": None, "note": note} for sp in SPORT_KEYS}
    out = {}
    for sport in ("nfl", "cfb"):
        block = {"label": LABEL, "coverage": {}, "games": [], "audit": None, "sensitivity": None, "note": note}
        out[sport] = block
        L = last_observation(live_conn, sport, T)
        if L is None:
            block.update(status="NO_DATA", coverage={"status": "NO_DATA", "reason": "no odds collected yet"})
            continue
        D = complete_through(L, T)
        ws, we = week_window(D)
        # runs from earlier model versions: kept as an immutable record, never advanced or shown
        block["superseded_runs"] = [
            {"run_id": rid[:12], "slate_id": sid, "engine_version": ev, "wagers": n}
            for rid, sid, ev, n in book_conn.execute(
                "SELECT r.run_id, r.slate_id, r.engine_version, (SELECT COUNT(*) FROM v4_wager w WHERE w.run_id=r.run_id) "
                "FROM v4_run r WHERE r.sport=? AND NOT (r.engine_version=? AND r.params_hash=?) ORDER BY r.window_start",
                (sport, ENGINE_VERSION, P.params_hash())).fetchall()]
        # finish earlier weeks (current model) that were still open at the previous build
        for rid, in book_conn.execute("SELECT run_id FROM v4_run WHERE sport=? AND window_start<? "
                                      "AND engine_version=? AND params_hash=?",
                                      (sport, from_epoch(ws), ENGINE_VERSION, P.params_hash())).fetchall():
            st, _ = advance_and_export(book_conn, live_conn, rid, D, book_out)
            if st != "OK":
                block.update(status="INTEGRITY_FAILED" if st.startswith("INTEGRITY") else "FROZEN", reason=st)
        if block.get("status"):
            continue
        row = book_conn.execute("SELECT run_id FROM v4_run WHERE sport=? AND slate_id=?",
                                (sport, slate_id(sport, ws))).fetchone()
        if row is None:
            games, coverage = select_week(live_conn, sport, ws, we, L, rankings_snapshots)
            block["coverage"] = coverage
            if coverage["status"] != "OK" or not games:
                block["status"] = coverage["status"] if coverage["status"] != "OK" else "NO_GAMES"
                continue
            matcher = None
            if sport == "cfb":
                snaps = [x for x in rankings_snapshots if x["fetched_at"] <= L and x.get("teams")]
                latest = max(snaps, key=lambda x: x["fetched_at"], default=None)
                matcher = rankings.TeamMatcher(latest["teams"]) if latest else None
            run_id = open_run(book_conn, live_conn, sport, ws, we, games,
                              universe(sport, rankings_snapshots, games), bettors, matcher,
                              standalone=standalone_ids(week_games(live_conn, sport, ws, we, L)),
                              settlements=settlements)
            ranks = {g["event_id"]: g.get("rankings") for g in games}
        else:
            run_id, ranks = row[0], {}
            n = len(load_run(book_conn, run_id)["slate"].games)
            block["coverage"] = {"status": "OK", "rule": ("all NFL games of the week with collected quotes"
                                                          if sport == "nfl" else "AP Top-25 games only"),
                                 "included": n, "fixed_at_open": True}
        cached = (sensitivity_cache or {}).get(sport) or {}
        last_verified = cached.get("replay_verified_at", 0) if cached.get("run_id") == run_id else 0
        verify = T - last_verified >= REPLAY_VERIFY_EVERY_S
        status, new = advance_and_export(book_conn, live_conn, run_id, D, book_out, verify=verify)
        verified_at = T if verify else last_verified
        if status != "OK":
            block.update(status="INTEGRITY_FAILED" if status.startswith("INTEGRITY") else "FROZEN", reason=status)
            continue
        run = load_run(book_conn, run_id)
        in_run = {g.canonical_game_id for g in run["slate"].games}
        eligible, _cov = select_week(live_conn, sport, ws, we, L, rankings_snapshots)
        block["coverage"]["listed_after_open"] = len({g["event_id"] for g in eligible} - in_run)
        wagers = fetch_wagers(book_conn, run_id, market=None)
        per_game = _by_game(wagers)
        wm = _watermark_iso(book_conn, run_id)
        pop_id, pop_hash = book_conn.execute(
            "SELECT p.population_id, p.population_hash FROM v4_run r JOIN v4_population p "
            "ON p.population_id=r.population_id WHERE r.run_id=?", (run_id,)).fetchone()
        info = loaded.get(run_id, {})
        n_int = book_conn.execute("SELECT COUNT(*), COALESCE(SUM(arrivals),0) FROM v4_interval WHERE run_id=?",
                                  (run_id,)).fetchone()
        block["audit"] = {
            "run_id": run_id, "slate_id": run["slate"].slate_id, "population_id": pop_id,
            "population_hash": pop_hash, "params_hash": run["params_hash"], "engine_version": run["engine_version"],
            "window": [_iso(to_epoch(run["slate"].window_start)), _iso(to_epoch(run["slate"].window_end))],
            "advanced_to": _iso(to_epoch(wm)),
            "data_complete_through": _iso(D), "last_observation": _iso(L), "bettors": bettors,
            "bettors_with_wagers": len({w["bettor_index"] for w in wagers}), "arrivals": n_int[1],
            "wagers": len(wagers), "ledger_intervals": n_int[0], "ledger_files_loaded": info.get("files", 0),
            "wagers_loaded_from_ledger": info.get("wagers", 0), "new_wagers_this_build": new,
            # full from-scratch replay: this build, or the last one (every REPLAY_VERIFY_EVERY_S)
            "replay_verified": "this build" if verify else "last at " + _iso(verified_at),
            "replay_verified_at": _iso(verified_at),
            "ledger_hash": json_hash([[w["wager_id"], w["market"], w["side"], w["accepted_line"],
                                       w["accepted_price"], w["stake"], w["accepted_at"]] for w in wagers]),
            "market_source": run["market_source_id"]}
        block["status"] = "OK" if wagers else "NO_WAGERS"

        # sensitivity: the same week re-simulated under each variant (common random numbers)
        # sensitivity: the same week re-simulated under each variant (common random numbers). Re-run at most
        # every SENSITIVITY_REFRESH_S per weekly run; in between the last variant splits are reused, labelled
        # with their time (the baseline split is always current).
        cached = (sensitivity_cache or {}).get(sport) or {}
        if (cached.get("run_id") == run_id and sorted(cached.get("variants") or []) == sorted(variants or {})
                and T - cached.get("computed_at", 0) < SENSITIVITY_REFRESH_S):
            var_splits, bands_at, reused = cached["variant_splits"], cached["computed_at"], True
        else:
            var_splits, bands_at, reused = {}, T, False
            for name, kw in (variants or {}).items():
                mem, rid = replay(book_conn, live_conn, run_id, wm, _iso(D), variant=(name, kw))
                for gid, ws_ in _by_game(fetch_wagers(mem, rid, market=None)).items():
                    var_splits.setdefault(gid, {})[name] = splits(ws_)
                mem.close()
        block["sensitivity_cache"] = {"run_id": run_id, "variants": sorted(variants or {}), "computed_at": bands_at,
                                      "variant_splits": var_splits, "replay_verified_at": verified_at}
        block["sensitivity"] = {"variants": sorted(variants or {}), "variants_as_of": _iso(bands_at), "reused": reused,
                                "method": "min/max of each split across the baseline and every variant"}
        # Need tracker: immutable pre-kickoff snapshots of every game in its final SNAPSHOT_HORIZON_S
        wm_t = to_epoch(wm)
        week_handles = {g.canonical_game_id: math.fsum(w["stake"] for w in per_game.get(g.canonical_game_id, [])
                                                       if w["market"] == "spread") for g in run["slate"].games}
        block["need_snapshots"], block["tracker_games"] = [], _tracker_games(book_conn, live_conn, sport)
        mkt = _market(live_conn, sport)
        for g in sorted(run["slate"].games, key=lambda x: (to_epoch(x.commence_time), x.canonical_game_id)):
            gid = g.canonical_game_id
            home, away = _names(live_conn, gid)
            ws_ = per_game.get(gid, [])
            ref_spread, ref_total, ref_at = _reference(live_conn, gid, L)
            sp = splits(ws_)
            need = book_need_all(ws_, ref_home_spread=ref_spread, ref_total=ref_total,
                                 max_score=MAX_SCORE[sport]) if ws_ else None
            rk = ranks.get(gid)
            if rk is None and sport == "cfb" and rankings_snapshots:
                rk = rankings.top25_status(home, away, to_epoch(g.commence_time),
                                           [x for x in rankings_snapshots if x["fetched_at"] <= L])
            kick = to_epoch(g.commence_time)
            if wm_t < kick <= wm_t + need_tracker.SNAPSHOT_HORIZON_S and T < kick:
                block["need_snapshots"].append(need_tracker.snapshot(
                    sport=sport, run=block["audit"],
                    game={"event_id": gid, "home": home, "away": away, "kickoff": _iso(kick), "rankings": rk},
                    spread_wagers=[w for w in ws_ if w["market"] == "spread"], week_handles=week_handles,
                    offer=representative_offer(mkt.latest_quotes(gid, wm_t)),
                    reference={"home_spread": ref_spread, "as_of": _iso(ref_at),
                               "basis": "observed consensus at last collection"},
                    taken_at=wm_t, built_at=T))
            block["games"].append({
                "event_id": gid, "home": home, "away": away, "kickoff": _iso(to_epoch(g.commence_time)), "rankings": rk,
                "reference": {"home_spread": ref_spread, "total": ref_total, "as_of": _iso(ref_at),
                              "basis": "observed consensus at last collection"},
                "splits": sp, "bands": _bands(sp, var_splits.get(gid, {})),
                "need": _need_summary(need) if need else None, "label": LABEL})
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
