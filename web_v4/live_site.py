"""
web_v4/live_site.py -- the automatically rebuilt V4 site, from COLLECTED data only.

Inputs (all already produced by the GitHub Actions pipeline; no API calls here):
  * odds shards (release odds-data-*)  -> market store (OBSERVED quotes)
  * credit ledger (release v4-state)   -> collection health
  * CFBD AP-poll snapshots (release cfb-rankings, optional) -> CFB Top-25 gating
Outputs: site_snapshot.json and index.html (one self-contained page: no external
scripts, fonts or requests; data embedded as JSON).

Labels the page always shows:
  OBSERVED      real quotes collected by V4, with the time they were observed
  MODELED       synthetic sportsbook customers (sportsbook_v4.live_book); never real splits
  EXPERIMENTAL  uncalibrated heuristics (sharp-money score)
  PAPER/WATCH   EV candidates from an unvalidated strategy: no official plays
Freshness is computed IN THE BROWSER from the observation time, so a page opened
hours later downgrades itself (LIVE <= 45 min, DELAYED <= 6 h, STALE beyond) and
never presents old odds as current.

  python -m web_v4.live_site --shards shards --ledger state/ledger.jsonl \\
      --rankings-dir rankings --out site
"""

import argparse
import calendar
import glob
import json
import os
import sqlite3
import time

from data_v4 import rankings
from sportsbook_v4 import live_book
from sportsbook_v4.schema import ensure_schema
from web_v4 import live

SCHEMA = "v4-live-site-1"
FRESH = {"live_max_min": 45, "delayed_max_min": 360}
SPORTS = {"nfl": "americanfootball_nfl", "cfb": "americanfootball_ncaaf"}


def _iso(t):
    return None if t is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _epoch(iso):
    return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")) if iso else None


def load_rankings(rankings_dir):
    if not rankings_dir or not os.path.isdir(rankings_dir):
        return []
    return [json.load(open(p)) for p in sorted(glob.glob(os.path.join(rankings_dir, "rankings_*.json")))]


def _rank_tag(snaps, home, away, kickoff_iso):
    if not snaps:
        return {"status": "UNAVAILABLE", "reason": "AP rankings not connected (CollegeFootballData key)"}, None
    st = rankings.top25_status(home, away, _epoch(kickoff_iso), snaps)
    return st, (st["top25"] if st["status"] == "OK" else None)


def _clean(o):
    if isinstance(o, float):
        return None if o != o else round(o, 6)
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_clean(v) for v in o]
    return o


def build_snapshot(conn, ledger_rows, snaps, now, book_conn=None, bettors=10_000, commit=None, book_in=None,
                   book_out=None, sensitivity_cache=None, settlements=None, line_shop_cache=None):
    """Everything the page shows, from the market store as of `now`."""
    out = {"schema": SCHEMA, "generated_at": _iso(now), "commit": commit, "freshness_policy": FRESH,
           "sports": {}, "collection": live.collection_health(conn, ledger_rows, now),
           "plays": [], "plays_note": "No strategy has passed the evidence gate (rebuild plan section G). "
                                      "Default decision: NO BET.",
           "splits_real": {"status": "UNAVAILABLE",
                           "reason": "No licensed betting-splits source is connected. Only MODELED splits "
                                     "from the synthetic sportsbook are shown, and they are not market data."},
           "sims": {"status": "NOT CONNECTED",
                    "detail": "The original ArbitrEdge SIMS source has not been provided, so no SIMS projections "
                              "are shown and none are substituted. V4's Engine C (NFL) ties the closing market "
                              "in walk-forward tests and is used only for key-number pricing in the EV scanner."}}
    latest_snap = max(snaps, key=lambda s: s["fetched_at"]) if snaps else None
    for sport, key in SPORTS.items():
        b = live.board(conn, key, now)
        rows, hist = live.scanner_rows(conn, key, now, cache=line_shop_cache)
        sharp = live.sharp_rows(conn, key, now)
        s = {"key": key, "label": "NFL" if sport == "nfl" else "College football (AP Top-25)",
             "observed_at": b["observed_at"], "games": b["games"], "scanner": rows, "sharp": sharp,
             "performance": hist}
        if sport == "cfb":
            tags = {}
            for g in s["games"]:
                g["rankings"], g["top25"] = _rank_tag(snaps, g["home"], g["away"], g["kickoff"])
                tags[g["event_id"]] = g["top25"]
            for r in s["scanner"]:
                r["top25"] = tags.get(r["game_id"], _rank_tag(snaps, r["home"], r["away"], r["kickoff"])[1])
            for r in s["sharp"]:
                r["top25"] = tags.get(r["event_id"], _rank_tag(snaps, r["home"], r["away"], r["kickoff"])[1])
            s["rankings"] = ({"status": "OK", "source": latest_snap["source"], "fetched_at": _iso(latest_snap["fetched_at"]),
                              "poll": rankings.AP_POLL} if latest_snap else
                             {"status": "UNAVAILABLE", "reason": "AP rankings not connected: the CollegeFootballData "
                                                                 "free API key has not been added yet."})
        out["sports"][sport] = s
    if book_conn is not None:
        bk = live_book.build(book_conn, conn, now, snaps, bettors=bettors, book_in=book_in, book_out=book_out,
                             sensitivity_cache=sensitivity_cache, settlements=settlements)
        out["_sensitivity_cache"] = {sp: bk[sp].pop("sensitivity_cache", None) for sp in SPORTS}
        for sport in SPORTS:
            out["sports"][sport]["book"] = bk[sport]
    return _clean(out)


def tracker_step(snap, snaps, now, need_dir=None, results_dir=None, need_out=None, conn=None):
    """Book Need Tracker: save this build's pre-kickoff snapshots, seal decisions for games that have
    kicked off, grade decisions against final scores. Files go to need_out/<release tag>/ (new only)."""
    from data_v4 import results as R
    from sportsbook_v4 import need_tracker as NT
    try:
        raw = NT.load(need_dir, "need_*.json.gz")
        decisions = NT.unique_decisions(NT.load(need_dir, "decisions_*.json.gz"))
        docs = R.load(results_dir)
    except (NT.TrackerIntegrityError, ValueError) as e:
        snap["need_tracker"] = {"status": "INTEGRITY_FAILED", "reason": str(e), "label": NT.LABEL}
        return
    fresh, games = [], []
    for sport in SPORTS:
        bk = snap["sports"][sport].get("book") or {}
        recs = bk.pop("need_snapshots", None) or []
        games += bk.pop("tracker_games", None) or []
        if recs and need_out:
            NT.write(os.path.join(need_out, NT.release_for(now)), NT.snapshot_name(sport, now), recs)
        fresh += recs
    new = NT.seal(games, raw + fresh, decisions, now)
    if new and need_out:
        NT.write(os.path.join(need_out, "v4-need-decisions"), NT.decisions_name(now), new)
    latest_snap = max((x for x in snaps if x.get("teams")), key=lambda x: x["fetched_at"], default=None)
    matcher = rankings.TeamMatcher(latest_snap["teams"]) if latest_snap else None
    all_dec = decisions + new
    res = R.match([{k: d[k] for k in ("sport", "event_id", "home", "away", "kickoff")} for d in all_dec], docs, matcher)
    from market_v4 import asof
    closes = {}
    for d in all_dec if conn is not None else ():
        st, _why = asof.observed_close(conn, d["event_id"], "spreads")
        if st:
            closes[(d["sport"], d["event_id"])] = {"line": st["line"], "fair": st["fair"]}
    # one record per sportsbook engine: V4 (m4) and V5 decisions are graded and summarised separately, never combined
    by_engine = {}
    for d in all_dec:
        by_engine.setdefault((d.get("snapshot") or {}).get("engine_version") or "no pre-kickoff snapshot", []).append(d)
    reports = {ev: NT.report(ds, res, closes) for ev, ds in sorted(by_engine.items())}
    watch = {}
    for r in fresh:      # latest snapshot per upcoming game (this build)
        watch[(r["sport"], r["event_id"])] = r
    snap["need_tracker"] = {
        "status": "OK", "label": NT.LABEL, "version": NT.TRACKER_VERSION, "thresholds": NT.QUALIFY,
        "snapshots_this_build": len(fresh), "decisions_sealed_this_build": len(new),
        "decisions_total": len(all_dec), "results_files": len(docs),
        "results_matched": len(res), "corrections": sum(1 for v in res.values() if v["corrected"]),
        "upcoming": sorted(watch.values(), key=lambda r: (r["kickoff"], r["sport"])),
        "reports_by_engine": reports, "active_engine": live_book.ENGINE_VERSION}


def official_step(snap, snaps, now, need_dir=None, results_dir=None, need_out=None):
    """Official ArbitrEdge Needs (experimental): save this build's frozen pre-kickoff records, seal the last one before
    kickoff for games that have started, grade sealed selections. Separate files (official_snap_*, official_dec_*)
    in the existing Need-tracker releases; never mixed with the Need tracker or C1-C4. Never fails the build."""
    from data_v4 import results as R
    from official_v5 import rules as OFF
    from sportsbook_v4 import need_tracker as NT
    fresh = []
    for sport in SPORTS:
        bk = snap["sports"][sport].get("book") or {}
        recs = bk.pop("official_snapshots", None) or []
        if recs and need_out:
            NT.write(os.path.join(need_out, NT.release_for(now)), OFF.snapshot_name(sport, now), recs)
        fresh += recs
    try:
        old = NT.load(need_dir, OFF.SNAP_PREFIX + "*.json.gz")
        decisions = OFF.unique(NT.load(need_dir, OFF.DEC_PREFIX + "*.json.gz"))
        docs = R.load(results_dir)
    except (NT.TrackerIntegrityError, ValueError) as e:
        snap["official_needs"] = {"status": "INTEGRITY_FAILED", "reason": str(e), "label": OFF.LABEL}
        return
    new = OFF.seal(old + fresh, decisions, now)
    if new and need_out:
        NT.write(os.path.join(need_out, "v4-need-decisions"), OFF.decisions_name(now), new)
    all_dec = decisions + new
    latest_snap = max((x for x in snaps if x.get("teams")), key=lambda x: x["fetched_at"], default=None)
    matcher = rankings.TeamMatcher(latest_snap["teams"]) if latest_snap else None
    res = R.match([{k: d[k] for k in ("sport", "event_id", "home", "away", "kickoff")} for d in all_dec], docs, matcher)
    from official_v5 import strategies as STR
    snap["official_needs"] = {"status": "OK", "label": OFF.LABEL, "strategies": list(STR.STRATEGIES),
                              "superseded": [OFF.RULES_VERSION, *STR.RETIRED],
                              "records_this_build": len(fresh), "sealed_this_build": len(new),
                              "sealed_total": len(all_dec), "reports": OFF.report_all(all_dec, res)}


def settlement_step(snap, bconn, conn, snaps, now, results_dir=None, settle_dir=None, settle_out=None):
    """Settle every accepted simulated wager whose game has a final score; derive bankrolls; reconcile."""
    from data_v4 import results as R
    from sportsbook_v4 import ENGINE_VERSION, bettor_settlement as BS, need_tracker as NT, params as P
    from sportsbook_v4.util import to_epoch
    try:
        existing = NT.load(settle_dir, "settle_*.json.gz")
        docs = R.load(results_dir)
    except (NT.TrackerIntegrityError, ValueError) as e:
        snap["settlement"] = {"status": "INTEGRITY_FAILED", "reason": str(e), "label": "MODELED"}
        return
    cols = ("wager_id", "run_id", "population_id", "bettor_index", "archetype", "canonical_game_id", "sport",
            "market", "side", "accepted_line", "accepted_price", "stake")
    wagers = [dict(zip(cols, r)) for r in bconn.execute(f"SELECT {', '.join(cols)} FROM v4_wager")]
    active = {r[0] for r in bconn.execute("SELECT run_id FROM v4_run WHERE engine_version=? AND params_hash=?",
                                          (ENGINE_VERSION, P.params_hash()))}
    games, kicks = {}, {}
    for rid, sport, gj in bconn.execute("SELECT run_id, sport, games_json FROM v4_run"):
        for g in json.loads(gj):
            home, away = live_book._names(conn, g["canonical_game_id"])
            games[(sport, g["canonical_game_id"])] = {"sport": sport, "event_id": g["canonical_game_id"], "home": home,
                                                      "away": away, "kickoff": g["commence_time"]}
            kicks[(sport, g["canonical_game_id"])] = to_epoch(g["commence_time"])
    latest_snap = max((x for x in snaps if x.get("teams")), key=lambda x: x["fetched_at"], default=None)
    matcher = rankings.TeamMatcher(latest_snap["teams"]) if latest_snap else None
    res = R.match([g for g in games.values() if g["home"]], docs, matcher)
    res = {k: v for k, v in res.items() if kicks.get(k, now) < now}          # never before kickoff
    new = BS.new_settlements(wagers, res, existing, _iso(now))
    if new and settle_out:
        NT.write(os.path.join(settle_out, "v4-settlement"), f"settle_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))}.json.gz", new)
    allset = existing + new
    problems = BS.verify(allset, {w["wager_id"]: w for w in wagers})
    bettors = [dict(zip(("population_id", "bettor_index", "archetype", "bankroll"), r))
               for r in bconn.execute("SELECT population_id, bettor_index, archetype, bankroll FROM v4_bettor")]
    rows, overdue = BS.bankrolls(bettors, wagers, allset, active, now, kicks)
    rec = BS.reconcile(wagers, allset, active, rows)
    snap["settlement"] = {"status": "OK" if not problems and rec["ok"] else "CHECK_FAILED", "label": "MODELED",
                          "rules_version": BS.RULES_VERSION, "settlement_rows": len(allset),
                          "new_this_build": len(new), "corrections": sum(1 for s in allset if s["supersedes"]),
                          "open_overdue_48h": overdue, "problems": problems[:20], "reconciliation": rec,
                          "summary": BS.summary(rows, wagers, allset, active)}
    try:
        snap["sharp_follow"] = sharp_follow_step(bconn, conn, active, games, res, now)
    except Exception as e:                       # a research display must never fail the build
        snap["sharp_follow"] = {"status": "UNAVAILABLE", "reason": f"{type(e).__name__}: {e}", "label": "EXPERIMENTAL"}


def feed_kickoffs(snap, conn, now):
    """Display fix: the kickoff of every UPCOMING game shown is the one the odds feed reports NOW. The feed lists
    CFB games without a set time at a placeholder (e.g. noon ET) and moves them later; the synthetic book's weekly
    run froze the time it saw when it opened, and its trading, snapshots and seals keep that time (immutable
    ledger, unchanged). Rows whose time moved keep the book's time in `book_kickoff`."""
    from market_v4 import asof
    feed = {e: k for e, (k, _h, _a) in asof.kickoffs(conn).items() if k > now}

    def walk(o):
        if isinstance(o, dict):
            eid, k = o.get("event_id"), o.get("kickoff")
            if isinstance(eid, str) and isinstance(k, str) and eid in feed:
                fk = _iso(feed[eid])
                if fk != k:
                    o["book_kickoff"], o["kickoff"] = k, fk
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(snap)


def sharp_follow_step(bconn, conn, active, games, res, now):
    """SHARP FOLLOW LIST: simulated sharps' bigger side per spread/total, its tags and graded records."""
    from market_v4 import asof
    from sportsbook_v4 import sharp_follow as SF
    from sportsbook_v4.util import to_epoch
    cols = ("run_id", "canonical_game_id", "sport", "market", "side", "archetype", "stake", "bettor_index",
            "accepted_line", "accepted_home_spread", "accepted_price", "accepted_at", "wager_id")
    by_game = {}
    for r in bconn.execute(f"SELECT {', '.join(cols)} FROM v4_wager WHERE market IN ('spread', 'total')"):
        w = dict(zip(cols, r))
        if w["run_id"] in active:
            by_game.setdefault((w["sport"], w["canonical_game_id"]), []).append(w)
    gs = {k: {**g, "kickoff": _iso(to_epoch(g["kickoff"]))} for k, g in games.items() if k in by_game}

    def close_line(sport, eid, market):
        ko = to_epoch(gs[(sport, eid)]["kickoff"])
        st = asof.market_state(conn, eid, "spreads" if market == "spread" else "totals", ko - 1e-6,
                               max_age_s=24 * 3600)
        return st.get("line") if st.get("status") == "OK" else None

    return SF.report(gs, by_game, res, close_line, _iso(now))


def circa_step(snap, circa_dir=None, circa_out=None, get=None, now=None):
    """CIRCA 99% ENGINE (splits_v4.circa_engine): every uploaded sheet's record, signals de-duplicated (first upload
    wins), graded fade / follow / lean with ESPN final scores. Finals are saved once per league and day when every
    signal game that day is final (circa_finals_<league>_<date>.json, write-once). Never stops the build."""
    try:
        from splits_v4 import circa_engine as _CE
        from splits_v4 import espn as _ESPN
        files = sorted(glob.glob(os.path.join(circa_dir, "**", "circa_*.json"), recursive=True)) if circa_dir else []
        recs = [p for p in files if not os.path.basename(p).startswith("circa_finals_")]
        docs = sorted((json.load(open(p)) for p in recs), key=lambda d: d.get("uploaded_at") or 0)
        bad = lambda d: bool(d.get("error")) or not d.get("rows_read")                     # noqa: E731
        ok_images = {d.get("image") for d in docs if not bad(d)}
        docs = [d for d in docs if not (bad(d) and d.get("image") in ok_images)]           # a failed read later retried
        finals = {}
        for p in files:
            if os.path.basename(p).startswith("circa_finals_"):
                finals.update(json.load(open(p)).get("finals", {}))
        seen, sigs, uploads = set(), [], []
        for d in docs:
            n_sig = sum(1 for s in d.get("signals", []) if s.get("status") == "SIGNAL")
            uploads.append({k: d.get(k) for k in ("image", "uploaded_at_iso", "rows_read", "games_ok", "error", "espn_games")}
                           | {"signals": n_sig, "problems": sum(1 for r in d.get("read", []) if r.get("status") != "OK"),
                              "unmatched": sum(1 for s in d.get("signals", []) if s.get("status") != "SIGNAL")})
            for s in d.get("signals", []):
                k = (s.get("event_id") or s.get("sheet_game"), s.get("market"), s.get("heavy"))
                if s.get("status") == "SIGNAL" and k not in seen:
                    seen.add(k)
                    sigs.append(dict(s, image=d.get("image")))
        need = {}
        for s in sigs:
            if s["event_id"] not in finals and s["kickoff"] < (now or time.time()) - 2 * 3600:
                need.setdefault((s["league"], s["game_date"]), set()).add(s["event_id"])
        for (lg, day), ids in need.items():
            try:
                got, err = _ESPN.games(day, leagues=(lg,), get=get) if get else _ESPN.games(day, leagues=(lg,))
            except Exception:                                    # noqa: BLE001
                continue
            fin = {g["id"]: [g["home_score"], g["away_score"]] for g in got if g["final"]
                   and g["home_score"] is not None and g["away_score"] is not None}
            finals.update(fin)
            if circa_out and ids <= set(fin):
                os.makedirs(circa_out, exist_ok=True)
                with open(os.path.join(circa_out, f"circa_finals_{lg}_{day}.json"), "w") as fh:
                    json.dump({"league": lg, "date": day, "source": "ESPN scoreboard", "finals": fin}, fh, sort_keys=True)
        for s in sigs:
            f = finals.get(s["event_id"])
            s["final"] = f
            s["graded"] = _CE.grade(s, tuple(f) if f else None)
            s["kickoff_iso"] = _iso(s["kickoff"])
        snap["circa"] = {"status": "OK", "rule": _CE.RULE, "signals": sigs, "uploads": uploads[-10:][::-1],
                         "n_uploads": len(docs)}
    except Exception as e:                 # noqa: BLE001
        snap["circa"] = {"status": "ERROR", "reason": f"{type(e).__name__}: {e}"[:300]}


def splits_step(snap, conn, rankings_snaps, results_dir, now, splits_dir=None, splits_out=None):
    """PUBLIC SPLITS PLAYS (splits_v4): seal play decisions for snapshots that have none yet (write-once files), then
    show every sealed play with its result. Display/record only; an error is shown and never stops the build."""
    try:
        from data_v4 import rankings as _RK
        from data_v4 import results as _RS
        from splits_v4 import plays as _P
        snaps_f = sorted(glob.glob(os.path.join(splits_dir, "**", "splits_*.json"), recursive=True)) if splits_dir else []
        sealed = {os.path.basename(p)[len("plays_"):]: p for p in
                  glob.glob(os.path.join(splits_dir, "**", "plays_*.json"), recursive=True)} if splits_dir else {}
        records, new = [], 0
        for p in snaps_f:
            name = os.path.basename(p)[len("splits_"):]
            if name in sealed:
                records += json.load(open(sealed[name]))["records"]
                continue
            doc = json.load(open(p))
            top = {e["event_id"]: (None if e["sport"] != "cfb" else
                                   (lambda st: st["top25"] if st["status"] == "OK" else None)(
                                       _RK.top25_status(e["home"], e["away"], e["kickoff"], rankings_snaps)))
                   for e in doc["events"]}
            recs = _P.decide(doc, conn, top)
            records += recs
            if splits_out:
                os.makedirs(splits_out, exist_ok=True)
                with open(os.path.join(splits_out, "plays_" + name), "w") as fh:
                    json.dump({"rule": _P.RULE, "snapshot": os.path.basename(p), "sealed_at": _iso(now), "records": recs},
                              fh, separators=(",", ":"), sort_keys=True)
                new += 1
        latest = max(records, key=lambda r: r["taken_at"])["taken_at"] if records else None
        gl = [{"sport": r["sport"], "event_id": r["event_id"], "home": r["game"].split(" @ ")[1],
               "away": r["game"].split(" @ ")[0], "kickoff": _iso(r["kickoff"])} for r in records]
        latest_rank = max((x for x in rankings_snaps if x.get("teams")), key=lambda x: x["fetched_at"], default=None)
        docs = _RS.load(results_dir) if results_dir else []
        finals = {k[1]: (v["home_score"], v["away_score"]) for k, v in
                  _RS.match(gl, docs, _RK.TeamMatcher(latest_rank["teams"]) if latest_rank else None).items()}
        for r in records:
            r["final"] = list(finals[r["event_id"]]) if r["event_id"] in finals else None
            r["graded"] = _P.grade(r, finals.get(r["event_id"]))
            r["kickoff_iso"] = _iso(r["kickoff"])
        snap["splits_plays"] = {"status": "OK", "rule": _P.RULE, "records": records, "sealed_this_build": new,
                                "snapshots": len(snaps_f), "latest_snapshot": _iso(latest) if latest else None}
    except Exception as e:                 # noqa: BLE001
        snap["splits_plays"] = {"status": "ERROR", "reason": f"{type(e).__name__}: {e}"[:300]}


def book_moves_step(snap, conn, rankings_snaps, results_dir, now):
    """PINNACLE & CIRCA line moves with an INFERRED need side (analysis_v4/book_moves.py). Display only; an error
    is shown on the tab and never stops the build."""
    try:
        from analysis_v4 import book_moves as _BM
        from data_v4 import rankings as _RK
        from data_v4 import results as _RS
        latest = max((x for x in rankings_snaps if x.get("teams")), key=lambda x: x["fetched_at"], default=None)
        matcher = _RK.TeamMatcher(latest["teams"]) if latest else None
        evs = conn.execute("SELECT event_id, sport, home, away, MAX(commence) FROM mkt_quote WHERE book IN "
                           "('pinnacle','circasports') AND market IN ('spreads','totals') GROUP BY event_id").fetchall()
        games = [{"sport": "nfl" if sp == "americanfootball_nfl" else "cfb", "event_id": e, "home": h, "away": a,
                  "kickoff": k} for e, sp, h, a, k in evs]
        docs = _RS.load(results_dir) if results_dir else []
        gl = [dict(g, kickoff=_iso(g["kickoff"])) for g in games]
        finals = {k[1]: (v["home_score"], v["away_score"]) for k, v in _RS.match(gl, docs, matcher).items()}
        snap["book_moves"] = _BM.build(conn, sorted(games, key=lambda g: g["kickoff"]), finals, now)
    except Exception as e:                 # noqa: BLE001
        snap["book_moves"] = {"status": "ERROR", "reason": f"{type(e).__name__}: {e}"[:300]}


def market_intel_step(snap, conn, rankings_snaps, results_dir, now, record_out=None, record_existing=None, bconn=None):
    """ORIGINAL ArbitrEdge market intelligence (spreads, original code) + totals extension mi_totals.v1.
    Flag-guarded: runs only when MARKET_INTEL=on (default off -> the page is unchanged). Display only; never stops
    the build. Optional write-once prospective records when MARKET_INTEL_RECORD=on (also default off)."""
    if os.environ.get("MARKET_INTEL") != "on":
        return
    import time as _t
    t0 = _t.time()
    try:
        from collect_v4 import backfill as _B
        from data_v4 import rankings as _RK
        from data_v4 import results as _RS
        from market_intel import LABEL as _MIL
        from market_intel import engine as _MI
        from market_intel import record as _MR
        polls = _B.poll_snapshots(rankings_snaps)
        latest = max((x for x in rankings_snaps if x.get("teams")), key=lambda x: x["fetched_at"], default=None)
        matcher = _RK.TeamMatcher(latest["teams"]) if latest else None
        evs = conn.execute("SELECT event_id, sport, home, away, MAX(commence) FROM mkt_quote WHERE market IN "
                           "('spreads','totals') GROUP BY event_id").fetchall()
        gl = [{"sport": "nfl" if sp == "americanfootball_nfl" else "cfb", "event_id": e, "home": h, "away": a,
               "kickoff": _iso(k)} for e, sp, h, a, k in evs]
        docs = _RS.load(results_dir) if results_dir else []
        finals = {k: (v["home_score"], v["away_score"]) for k, v in _RS.match(gl, docs, matcher).items()}
        from market_intel import agreement_research as _AR
        snap["market_intel"] = {"status": "OK", "label": _MIL, "cards": _MI.cards(conn, finals, polls, now, bconn),
                                "results_matched": len(finals), "agreement_research": _AR.load()}
        if record_out and _MR.enabled():
            ex = [x.strip() for x in open(record_existing)] if record_existing and os.path.exists(record_existing) else []
            snap["market_intel"]["records_written"] = len(_MR.record(conn, finals, polls, now, record_out, ex, bconn))
    except Exception as e:                 # noqa: BLE001
        snap["market_intel"] = {"status": "ERROR", "reason": f"{type(e).__name__}: {e}"[:300]}
    snap["market_intel"]["seconds"] = round(_t.time() - t0, 1)


def rebuild_store(shards_dir, db_path=":memory:"):
    from analysis_v4.actions_analyze import rebuild
    paths = sorted(glob.glob(os.path.join(shards_dir, "**", "quotes_*.jsonl.gz"), recursive=True))
    conn, counts = rebuild(paths, db_path)
    return conn, counts, len(paths)


# ------------------------------------------------------------------ page

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<title>ArbitrEdge V4</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--mute:#5d6470;--line:#e2e5ea;--acc:#2456c7;
--ok:#1d7a46;--warn:#9a6200;--bad:#b42318;--chip:#eef1f6;--obs:#1d7a46;--mod:#7a3fb0;--exp:#9a6200}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#e8eaee;--mute:#9aa2ae;--line:#2a2f39;
--acc:#7aa2ff;--ok:#4cc584;--warn:#e0a63a;--bad:#ff6b5e;--chip:#222733;--obs:#4cc584;--mod:#c08cf0;--exp:#e0a63a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);
padding:10px 16px calc(8px) 16px}
.top{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.brand{font-weight:700}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{border:0;background:transparent;color:var(--ink);padding:6px 12px;font:inherit;cursor:pointer}
.seg button.on{background:var(--acc);color:#fff}
nav{display:flex;gap:4px;overflow-x:auto;margin-top:8px;padding-bottom:2px;scrollbar-width:none}
nav a{white-space:nowrap;text-decoration:none;color:var(--mute);padding:5px 10px;border-radius:999px;font-size:14px}
nav a.on{background:var(--chip);color:var(--ink);font-weight:600}
main{max-width:1100px;margin:0 auto;padding:12px 16px 60px}
.mi2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px}@media(max-width:760px){.mi2{grid-template-columns:minmax(0,1fr)}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}
h2{font-size:18px;margin:6px 0 2px}h3{font-size:15px;margin:4px 0}
.mute{color:var(--mute)}.small{font-size:13px}
.badge{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.03em;padding:2px 7px;border-radius:999px;
border:1px solid currentColor;margin-right:4px;vertical-align:middle}
.b-LIVE,.b-OBSERVED,.b-OK{color:var(--ok)}.b-DELAYED,.b-EXPERIMENTAL,.b-WATCH,.b-LOW{color:var(--warn)}
.b-STALE,.b-UNAVAILABLE,.b-NOBET{color:var(--bad)}.b-MODELED{color:var(--mod)}.b-INFO{color:var(--mute)}
.banner{border-radius:8px;padding:8px 12px;margin:8px 0;font-size:14px}
.banner.stale{background:color-mix(in srgb,var(--bad) 14%,transparent);border:1px solid var(--bad)}
.banner.delayed{background:color-mix(in srgb,var(--warn) 14%,transparent);border:1px solid var(--warn)}
.banner.info{background:var(--chip)}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0}
.pos{color:var(--ok);font-weight:600}.neg{color:var(--bad)}tr.tot td{font-weight:700;border-top:2px solid var(--line)}
.filters label{font-size:12px;color:var(--mute);display:flex;flex-direction:column;gap:2px}
select,input{font:inherit;font-size:14px;padding:5px 7px;border:1px solid var(--line);border-radius:6px;
background:var(--card);color:var(--ink);max-width:180px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:6px 6px;border-bottom:1px solid var(--line);
vertical-align:top}th{font-size:12px;color:var(--mute);font-weight:600}
.num{text-align:right;font-variant-numeric:tabular-nums}
.game{cursor:pointer}.game:hover{background:var(--chip)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.kpi{background:var(--chip);border-radius:8px;padding:8px 10px}.kpi b{display:block;font-size:20px}
.bar{position:relative;height:16px;background:var(--chip);border-radius:4px;overflow:hidden;margin:2px 0}
.bar i{position:absolute;left:0;top:0;bottom:0;background:var(--acc);opacity:.75}
.bar s{position:absolute;top:0;bottom:0;border-left:2px solid var(--ink);border-right:2px solid var(--ink);opacity:.35}
.dim{opacity:.45}.wrap{overflow-x:auto}
.ml{display:none;font-size:11px;font-weight:700;color:var(--mute);text-transform:uppercase}
@media (max-width:640px){.hide-s{display:none}
.odds thead{display:none}.odds tr{display:grid;grid-template-columns:repeat(3,1fr);gap:4px 8px;padding:8px 0;border-bottom:1px solid var(--line)}
.odds td{border:0;padding:2px 0}.odds td:first-child{grid-column:1/-1;font-weight:600}.ml{display:block}}
</style></head><body>
<header><div class="top"><span class="brand" id="brand">ArbitrEdge</span>
<span class="seg" id="sport"><button data-s="nfl">NFL</button><button data-s="cfb">CFB Top-25</button></span>
<span id="fresh" class="small"></span></div>
<nav id="nav"></nav></header>
<main id="main"></main>
<script type="application/json" id="data">__DATA__</script>
<script>
"use strict";
const D=JSON.parse(document.getElementById("data").textContent);
{const ev=((((D.sports||{}).nfl||{}).book||{}).audit||{}).engine_version||"";
 document.getElementById("brand").textContent="ArbitrEdge "+(ev.startsWith("sportsbook_v5")?"V5":"V4")}
const SECTIONS_MI=D.market_intel?[["mi","Market Intelligence"]]:[];
const SECTIONS=[["overview","Overview"],["odds","Odds Board"],["splits","Betting Splits"],["need","ArbitrEdge Need"],
["tracker","Need Tracker"],["official","Official Needs"],["sharp","Sharp Money"],["moves","Pinnacle & Circa"],["ev","EV Scanner"],["sims","SIMS"],["performance","Performance"],["health","Health"]];
const MK={spreads:"Spread",totals:"Total",h2h:"Moneyline"};const BMK={spread:"spreads",total:"totals",moneyline:"h2h"};
const st={sport:"nfl",sec:"overview",game:null,f:{q:"",market:"",book:"",date:"",strength:"0",fresh:"all",unfiltered:false}};
try{const s=localStorage.getItem("v4sport");if(s==="nfl"||s==="cfb")st.sport=s;st.more=!!localStorage.getItem("v4more")}catch(e){}
function el(tag,attrs,...kids){const e=document.createElement(tag);for(const[k,v]of Object.entries(attrs||{})){
 if(k==="class")e.className=v;else if(k==="on")for(const[ev,fn]of Object.entries(v))e.addEventListener(ev,fn);else e.setAttribute(k,v)}
 for(const k of kids.flat()){if(k==null||k===false)continue;e.append(k instanceof Node?k:document.createTextNode(String(k)))}return e}
const badge=(t,c)=>el("span",{class:"badge b-"+(c||t).replace(/\s/g,"")},t);
const pct=x=>x==null?"–":(100*x).toFixed(1)+"%";const am=p=>p==null?"–":(p>0?"+"+p:String(p));
const ln=x=>x==null?"":(x>0?"+"+x:String(x));
function ageMin(iso){return iso?Math.max(0,(Date.now()-Date.parse(iso))/60000):Infinity}
function freshness(iso){const a=ageMin(iso),p=D.freshness_policy;return a<=p.live_max_min?"LIVE":a<=p.delayed_max_min?"DELAYED":"STALE"}
function fmtAge(m){if(!isFinite(m))return"never";if(m<90)return Math.round(m)+" min";if(m<48*60)return(m/60).toFixed(1)+" h";return(m/1440).toFixed(1)+" days"}
function local(iso){if(!iso)return"–";const d=new Date(iso);return d.toLocaleString([], {weekday:"short",month:"short",day:"numeric",hour:"numeric",minute:"2-digit"})}
function ko(g){return local(g.kickoff)+(g.book_kickoff?" (time moved; sim book closed "+local(g.book_kickoff)+")":"")}
const S=()=>D.sports[st.sport];
function cfbGate(rowTop25){if(st.sport!=="cfb")return true;if(st.f.unfiltered)return true;return rowTop25===true}
function upcoming(g){return Date.parse(g.kickoff)>Date.now()}
function gameMatch(g){const f=st.f;if(f.q&&!(g.home+" "+g.away).toLowerCase().includes(f.q.toLowerCase()))return false;
 if(f.date&&(new Date(g.kickoff)).toISOString().slice(0,10)!==f.date)return false;return true}
function games(){const s=S();return(s.games||[]).filter(g=>upcoming(g)&&cfbGate(g.top25)&&gameMatch(g))}
function gameById(id){for(const s of Object.values(D.sports))for(const g of s.games||[])if(g.event_id===id)return g;return null}
function freshBanner(){const s=S(),f=freshness(s.observed_at),a=fmtAge(ageMin(s.observed_at));
 if(!s.observed_at)return el("div",{class:"banner stale"},"No odds have been collected for this sport yet.");
 if(f==="STALE")return el("div",{class:"banner stale"},badge("STALE")," Odds were observed "+a+" ago ("+local(s.observed_at)+"). They are NOT current prices; shown for reference only.");
 if(f==="DELAYED")return el("div",{class:"banner delayed"},badge("DELAYED")," Odds observed "+a+" ago. Prices may have moved since.");
 return el("div",{class:"banner info"},badge("LIVE")," Odds observed "+a+" ago (collection cadence varies; faster near kickoff).")}
function cfbBanner(){if(st.sport!=="cfb")return null;const r=S().rankings||{};
 const pend=(S().games||[]).filter(g=>upcoming(g)&&g.rankings&&g.rankings.status==="PENDING").length;
 if(r.status==="OK")return el("div",{class:"banner info"},"AP Top-25 per game uses the poll applicable at kickoff. Source: "+r.source+", fetched "+local(r.fetched_at)+".",
  pend?" "+pend+" later game(s) wait for next week's poll (PENDING; shown with the non-Top-25 games).":"",
  st.f.unfiltered?el("b",{}," Showing ALL collected FBS games (unfiltered)."):null);
 return el("div",{class:"banner stale"},badge("UNAVAILABLE")," "+(r.reason||"AP rankings unavailable")+" CFB Top-25 views are empty until rankings are connected; nothing is guessed.",
  st.f.unfiltered?el("b",{}," Showing ALL collected FBS games, Top-25 status UNKNOWN."):null)}
function filters(opts){const f=st.f,set=(k)=>e=>{f[k]=e.target.type==="checkbox"?e.target.checked:e.target.value;render()};
 const sel=(k,label,choices)=>el("label",{},label,(()=>{const s=el("select",{on:{change:set(k)}});for(const[v,t]of choices){const o=el("option",{value:v},t);if(String(f[k])===v)o.selected=true;s.append(o)}return s})());
 const books=[...new Set((D.collection.books||[]).flatMap(b=>b.books))].sort();
 const dates=[...new Set((S().games||[]).filter(upcoming).map(g=>new Date(g.kickoff).toISOString().slice(0,10)))].sort();
 const kids=[el("label",{},"Game",el("input",{type:"search",placeholder:"team",value:f.q,on:{input:e=>{f.q=e.target.value;clearTimeout(window._t);window._t=setTimeout(render,250)}}})),
  sel("date","Date",[["","All dates"],...dates.map(d=>[d,d])])];
 if(opts.market)kids.push(sel("market","Market",[["","All markets"],["spreads","Spread"],["totals","Total"],["h2h","Moneyline"]]));
 if(opts.book)kids.push(sel("book","Sportsbook",[["","All books"],...books.map(b=>[b,b])]));
 if(opts.strength)kids.push(sel("strength","Signal strength (EV low bound)",[["0","Any"],["0.01","≥ 1%"],["0.02","≥ 2%"],["0.04","≥ 4%"]]));
 if(opts.fresh)kids.push(sel("fresh","Freshness",[["all","All quotes"],["live","Fresh at observation only"]]));
 if(st.sport==="cfb")kids.push(el("label",{},"CFB scope",el("span",{},(()=>{const c=el("input",{type:"checkbox",on:{change:set("unfiltered")}});c.checked=f.unfiltered;return c})()," show non-Top-25")));
 return el("div",{class:"filters"},kids)}
const card=(...k)=>el("div",{class:"card"},...k);
function gameHead(g){return el("span",{},g.away+" @ "+g.home,g.rankings&&g.rankings.status==="OK"?el("span",{class:"mute small"}," ("+(g.rankings.away_rank?"#"+g.rankings.away_rank:"NR")+" @ "+(g.rankings.home_rank?"#"+g.rankings.home_rank:"NR")+")"):null)}
function openGame(id){location.hash="game/"+id}

// ---------- sections
const R={};
R.overview=()=>{const s=S(),c=D.collection,gs=games(),bk=s.book||{};
 const scan=(s.scanner||[]).filter(r=>cfbGate(r.top25));
 return[freshBanner(),cfbBanner(),card(el("h2",{},"Overview: "+s.label),
  el("div",{class:"grid"},el("div",{class:"kpi"},el("b",{},gs.length),"upcoming games"),
   el("div",{class:"kpi"},el("b",{},scan.length),"EV candidates at last observation (WATCH, paper)"),
   el("div",{class:"kpi"},el("b",{},bk.audit?bk.audit.wagers:"–"),"modeled wagers this week (ledger)"),
   el("div",{class:"kpi"},el("b",{},D.plays.length),"official plays")),
  el("p",{},badge("NO BET","NOBET")," "+D.plays_note)),
  card(el("h3",{},"What each label means"),el("p",{class:"small"},badge("OBSERVED")," real quotes V4 collected · ",badge("MODELED")," synthetic customers, not real bettors · ",
   badge("EXPERIMENTAL")," uncalibrated heuristic · ",badge("WATCH")," paper candidate from an unvalidated strategy")),
  card(el("h3",{},"Line-shopping test (closing EV)"),perfLine(s.performance))]};
function perfLine(h){if(!h)return el("p",{class:"mute"},"No analysis yet.");
 const ci=h.closing_ev;return el("p",{},badge(h.verdict==="INSUFFICIENT DATA"?"INSUFFICIENT DATA":h.verdict,"INFO")," ",
  h.opportunities+" opportunities, "+h.close_observed+" with an observed close"+(ci&&ci.mean!=null?", mean closing EV "+pct(ci.mean)+" (95% CI "+pct(ci.lo)+" to "+pct(ci.hi)+")":"")+".")}
R.odds=()=>{const s=S(),f=st.f,ms=f.market?[f.market]:["spreads","totals","h2h"];const rows=[];
 for(const g of games()){const tr=el("tr",{class:"game",on:{click:()=>openGame(g.event_id)}},el("td",{},gameHead(g),el("div",{class:"mute small"},ko(g))));
  for(const m of ms){const x=g.markets[m]||{};const sides=Object.keys(x.best||{});
   let txt;if(x.status!=="OK")txt=el("span",{class:"mute"},x.status||"–");else{
    txt=el("div",{},...sides.map(sd=>{let b=x.best[sd];if(f.book){const q=(x.books||{})[f.book];b=q&&q[sd]?{book:f.book,price:q[sd][0],point:q[sd][1],fresh:q.fresh}:null}
     const label=m==="h2h"?sd:(m==="spreads"?sd+" "+ln(sd==="home"?x.line:(x.line==null?null:-x.line)):sd+" "+x.line);
     if(f.fresh==="live"&&b&&b.fresh===false)b=null;
     return el("div",{class:"small"},label+": ",el("b",{},b?am(b.price):"–"),b?el("span",{class:"mute"}," "+b.book):null," ",el("span",{class:"mute"},"fair "+pct(x.fair&&x.fair[sd])))}))}
   tr.append(el("td",{},el("span",{class:"ml"},MK[m]),txt))}
  rows.push(tr)}
 const stale=freshness(s.observed_at)==="STALE";
 return[freshBanner(),cfbBanner(),card(el("h2",{},badge("OBSERVED"),"Odds board"),el("p",{class:"small mute"},"Best price per side among books on the consensus line; fair = market-maker consensus with vig removed. Tap a game for every book."),
  filters({market:1,book:1,fresh:1}),el("div",{class:"wrap"+(stale?" dim":"")},el("table",{class:"odds"},el("thead",{},el("tr",{},el("th",{},"Game"),...ms.map(m=>el("th",{},MK[m])))),el("tbody",{},rows))),
  rows.length?null:el("p",{class:"mute"},"No upcoming games match."))]};
function splitBar(sd,b){const w=sd.ticket_pct,h=sd.handle_pct;return el("div",{},
 el("div",{class:"small"},"tickets "+w+"%",b?el("span",{class:"mute"}," (range "+b.ticket_pct[0]+"–"+b.ticket_pct[1]+")"):null),
 el("div",{class:"bar"},el("i",{style:"width:"+w+"%"}),b?el("s",{style:"left:"+b.ticket_pct[0]+"%;width:"+(b.ticket_pct[1]-b.ticket_pct[0])+"%"}):null),
 el("div",{class:"small"},"handle "+h+"%",b?el("span",{class:"mute"}," (range "+b.handle_pct[0]+"–"+b.handle_pct[1]+")"):null),
 el("div",{class:"bar"},el("i",{style:"width:"+h+"%"}),b?el("s",{style:"left:"+b.handle_pct[0]+"%;width:"+(b.handle_pct[1]-b.handle_pct[0])+"%"}):null),
  sd.potential_payout!=null?el("div",{class:"small mute"},"potential payout $"+Math.round(sd.potential_payout).toLocaleString()+" · book P&L if this side wins "+(sd.book_pnl_if_side_wins<0?"−$":"$")+Math.abs(Math.round(sd.book_pnl_if_side_wins)).toLocaleString()):null)}
function bookBlock(){const bk=S().book;if(!bk)return card(el("p",{class:"mute"},"Synthetic book not built."));
 if(bk.status==="INTEGRITY_FAILED"||bk.status==="FROZEN")return card(badge(bk.status,"UNAVAILABLE")," The stored synthetic-book ledger failed verification and is not shown: "+(bk.reason||""));
 if(["UNAVAILABLE","WAITING_FOR_POLL","NO_DATA"].includes(bk.status))return card(badge(bk.status==="WAITING_FOR_POLL"?"WAITING FOR POLL":bk.status,"UNAVAILABLE")," "+((bk.coverage||{}).reason||""));
 if(bk.status==="NO_GAMES")return card(el("p",{class:"mute"},"No games in this betting week."));return null}
function ledgerLine(){const a=(S().book||{}).audit;if(!a)return null;
 return el("p",{class:"small mute"},"Persistent ledger for the week "+local(a.window[0])+" to "+local(a.window[1])+": "+a.wagers.toLocaleString()+" wagers from "+a.bettors_with_wagers.toLocaleString()+" of "+a.bettors.toLocaleString()+" simulated bettors; "+a.wagers_loaded_from_ledger.toLocaleString()+" reloaded from earlier builds, "+(a.reconstructed_wagers?a.reconstructed_wagers.toLocaleString()+" RECONSTRUCTED from historical observed odds when the run opened ("+local(a.opened_at)+"), not accepted live; ":"")+a.new_wagers_this_build.toLocaleString()+" new. Full replay check: "+a.replay_verified+". Book advanced to "+local(a.advanced_to)+"; it trades only while a real price no older than 6 h exists (last odds observed "+local(a.last_observation)+").")}
function bookGames(){const bk=S().book||{};return(bk.games||[]).filter(g=>upcoming(g)&&gameMatch(g))}
R.splits=()=>{const f=st.f,bk=S().book||{};const head=[card(el("h2",{},"Betting splits"),
  el("p",{},badge("UNAVAILABLE")," Real splits: "+D.splits_real.reason),
  el("p",{class:"small"},badge("MODELED")," Below: the synthetic sportsbook's own customers ("+(bk.audit?bk.audit.bettors.toLocaleString():"10,000")+" persistent simulated bettors for this sport) betting into the real collected prices. Tickets come from wager counts and handle from stakes. Ranges show how far each split moves under alternative behaviour assumptions ("+((bk.sensitivity||{}).variants||[]).join(", ")+"; replays as of "+local((bk.sensitivity||{}).variants_as_of)+", refreshed at most every 3 h). These are not DraftKings, FanDuel or market-wide splits."),
  ledgerLine(),filters({market:1}))];
 const b=bookBlock();if(b)return[...head,b];const out=[];
 for(const g of bookGames()){const ms=f.market?[f.market]:["spreads","totals","h2h"];const rows=[];
  for(const[bm,row]of Object.entries(g.splits)){const m=BMK[bm];if(!ms.includes(m))continue;
   if(row.status!=="OK"){rows.push(el("div",{class:"small mute"},MK[m]+": no modeled wagers yet"));continue}
   rows.push(el("div",{},el("h3",{},MK[m]," ",row.sample==="LOW"?badge("LOW SAMPLE","LOW"):null,el("span",{class:"mute small"}," "+row.tickets+" tickets, $"+Math.round(row.handle).toLocaleString()+" handle")),
    ...Object.entries(row.sides).map(([sd,v])=>el("div",{},el("b",{class:"small"},sd),splitBar(v,(g.bands[bm]||{})[sd])))))}
  out.push(card(el("div",{class:"game",on:{click:()=>openGame(g.event_id)}},el("h3",{},badge("MODELED"),gameHead(g)),el("div",{class:"mute small"},ko(g))),...rows))}
 return[...head,...(out.length?out:[card(el("p",{class:"mute"},"No modeled wagers on upcoming games yet. The synthetic book only trades while real prices are being collected."))])]};
R.need=()=>{const bk=S().book||{};const head=[card(el("h2",{},"ArbitrEdge Need"),el("p",{class:"small"},badge("MODELED"),
 " The outcome the synthetic book's own liability most needs, computed exactly from its accepted wagers (P&L over every final score). It is a liability fact about simulated customers, NOT a prediction, NOT fading the public, and NOT a betting recommendation. Need is not betting value: it says nothing about whether a price is good. Whether betting the Need has any advantage is being tested, on paper, in the Need Tracker tab. Estimated value lives only in the EV Scanner, as unvalidated paper candidates."),ledgerLine(),filters({}))];
 const b=bookBlock();if(b)return[...head,b];const out=[];
 for(const g of bookGames()){const n=g.need;if(!n){continue}const sp=n.spread||{},to=n.total||{},ml=n.moneyline||{};
  out.push(card(el("div",{class:"game",on:{click:()=>openGame(g.event_id)}},el("h3",{},gameHead(g))),
   stability(g.need_stability,sd=>sd==="home"?g.home:g.away),
   g.reconstructed_wagers?el("div",{class:"small mute"},g.reconstructed_wagers+" of this game's wagers were reconstructed from historical observed odds when the week's run opened"):null,
   el("table",{},el("tbody",{},
    sp.status?el("tr",{},el("td",{},"Spread"),el("td",{},sp.financial_need_side?el("b",{},sp.financial_need_side+" covers"):"balanced"),el("td",{class:"num"},"swing $"+Math.round(sp.pnl_swing||0).toLocaleString())):null,
    to.status?el("tr",{},el("td",{},"Total"),el("td",{},to.financial_need_side?el("b",{},to.financial_need_side):"balanced"),el("td",{class:"num"},to.handle!=null?"handle $"+Math.round(to.handle).toLocaleString():"")):null,
    ml.status?el("tr",{},el("td",{},"Moneyline"),el("td",{},ml.financial_need_side?el("b",{},ml.financial_need_side+" wins"):"balanced"),el("td",{class:"num"},ml.handle!=null?"handle $"+Math.round(ml.handle).toLocaleString():"")):null)),
   n.joint?el("p",{class:"small mute"},"Joint book: best $"+Math.round(n.joint.best_case.pnl).toLocaleString()+" at "+n.joint.best_case.home_score+"–"+n.joint.best_case.away_score+" (home–away), worst $"+Math.round(n.joint.worst_case.pnl).toLocaleString()+" at "+n.joint.worst_case.home_score+"–"+n.joint.worst_case.away_score+"."):null))}
 return[...head,...(out.length?out:[card(el("p",{class:"mute"},"No modeled liability yet on upcoming games."))])]};
const money=x=>x==null?"–":(x<0?"−$":"$")+Math.abs(Math.round(x)).toLocaleString();
const CHECK={weekly_handle_top_25pct:"top 25% weekly handle",min_100_spread_wagers:"≥100 spread wagers",no_bettor_over_25pct:"no bettor >25% of handle",
 rec_handle_70pct_one_side:"≥70% rec handle one side",rec_ticket_majority_same_side:"rec ticket majority same side"};
const VAR_NAMES={fav_pref_half:"weaker favourite preference",["fav_pref_x1.5"]:"stronger favourite preference",sharp_share_6pct:"twice as many sharps",timing_all_steady:"everyone bets evenly through the week"};
function stability(ns,nm){if(!ns||ns.agree==null)return el("span",{class:"small mute"},"Need stability: not computed yet");
 const side=nm?nm(ns.baseline):ns.baseline;
 if(ns.label==="STABLE")return el("div",{class:"small"},badge("STABLE NEED","INFO")," the book needs "+side+" under all "+ns.variants+" alternative customer assumptions");
 return el("div",{class:"small"},badge("FRAGILE NEED","LOW")," the book needs "+side+" in the baseline, but the side changes under "+ns.flips_under.map(k=>VAR_NAMES[k]||k).join(", ")+" ("+ns.agree+" of "+ns.variants+" agree)")}
function sumLine(x){if(!x||!x.games)return el("p",{class:"mute"},"No games yet.");const r=x.roi;
 return el("div",{},el("div",{class:"grid"},el("div",{class:"kpi"},el("b",{},x.record),"W-L-P ATS ("+x.graded+" graded)"),
  el("div",{class:"kpi"},el("b",{},x.win_pct_ex_push==null?"–":pct(x.win_pct_ex_push)),"win % ex-push"+(x.win_pct_95ci?" (95% CI "+pct(x.win_pct_95ci[0])+"–"+pct(x.win_pct_95ci[1])+")":"")),
  el("div",{class:"kpi"},el("b",{},r?pct(r.roi):"–"),"ROI at recorded price"+(r&&r.roi_95ci?" (95% CI "+pct(r.roi_95ci[0])+" to "+pct(r.roi_95ci[1])+")":"")),
  el("div",{class:"kpi"},el("b",{},x.awaiting_result),"awaiting final score")),
  r?el("p",{class:"small mute"},"Break-even at the recorded prices: "+pct(r.breakeven_win_pct)+" · "+r.units.toFixed(2)+" units on "+r.n+" priced picks"):null,
  x.sample_warning?el("p",{class:"small"},badge("INSUFFICIENT SAMPLE","LOW")," "+x.sample_warning):null)}
function brkTable(o,t){return el("div",{},el("h3",{},t),el("table",{},el("tbody",{},Object.entries(o||{}).map(([k,v])=>el("tr",{},el("td",{},k),el("td",{class:"num"},v.record+" ("+v.graded+" graded)"),
 el("td",{class:"num"},v.win_pct_ex_push==null?"–":pct(v.win_pct_ex_push)),el("td",{class:"num"},v.roi?"ROI "+pct(v.roi.roi):"–"))))))}
function cohortCard(c){if(!c)return null;
 return card(el("h3",{},"Pre-registered paper cohorts"),el("p",{class:"small"},"Rules were frozen at "+local(c.registered_at)+" (registry "+c.registry_hash.slice(0,12)+"…) and are evaluated when each snapshot is taken, so a selection can never change after a result is known. C1 is the primary strategy; C2–C4 are exploratory comparisons. P-values test the win rate against the break-even of the recorded prices and are Holm-adjusted across all four cohorts."),
  ...Object.entries(c.cohorts).map(([id,x])=>{const sm=x.summary,cl=x.clv,r=sm.roi;
   return el("div",{style:"padding:8px 0;border-top:1px solid var(--line)"},el("div",{},el("b",{},id.split("_")[0]+" "+x.name)," ",badge(x.role,x.role==="PRIMARY"?"WATCH":"INFO")),
    el("div",{class:"small mute"},"Pick: "+x.pick+". Rules: "+Object.entries(x.rules).map(([k,v])=>k.replace(/_/g," ")+" "+v).join("; ")),
    el("div",{class:"small"},"ATS "+sm.record+" ("+sm.graded+" graded, "+sm.awaiting_result+" awaiting)"+(sm.win_pct_ex_push!=null?" · win "+pct(sm.win_pct_ex_push)+" (95% CI "+pct(sm.win_pct_95ci[0])+"–"+pct(sm.win_pct_95ci[1])+")":"")+
     (r?" · ROI "+pct(r.roi)+(r.roi_95ci?" ("+pct(r.roi_95ci[0])+" to "+pct(r.roi_95ci[1])+")":""):"")),
    el("div",{class:"small"},"CLV: close observed "+cl.close_observed+"/"+cl.picks+(cl.mean_clv_points!=null?" · mean "+ln(cl.mean_clv_points)+" pts, "+pct(cl.share_beating_close_points)+" beat the close":"")+(cl.mean_clv_prob!=null?" · same-number price CLV "+pct(cl.mean_clv_prob)+" (n "+cl.same_line_n+")":"")),
    el("div",{class:"small mute"},"p (edge vs break-even) "+(x.p_edge_one_sided==null?"–":x.p_edge_one_sided)+" · Holm-adjusted "+(x.p_edge_holm==null?"–":x.p_edge_holm)),
    sm.sample_warning?el("div",{class:"small"},badge("INSUFFICIENT SAMPLE","LOW")):null)}))}
R.tracker=()=>{const T=D.need_tracker;const head=card(el("h2",{},badge("MODELED"),"Book Need Tracker"),
 el("p",{class:"small"},badge("RESEARCH ONLY","WATCH")," Tests one question: when broad recreational betting pressure leaves our synthetic sportsbook with a large financial Need, does betting the side the book needs win often enough to beat the price? The Need comes from the book's actual simulated P&L (every accepted wager at its own line and price). The bettors are simulated with unvalidated assumptions, not real customers. Nothing here is a recommendation; every result is paper until it passes the evidence gate."),
 T&&T.thresholds?el("p",{class:"small mute"},"Pre-registered experimental filter (not validated): "+Object.values(CHECK).join(" · ")+". A qualified game is a research pick only when fading the public is also the side the book needs; when they disagree it is flagged, not picked."):null);
 if(!T)return[head,card(el("p",{class:"mute"},"Tracker not built yet."))];
 if(T.status!=="OK")return[head,card(badge(T.status,"UNAVAILABLE")," "+(T.reason||""))];
 const up=(T.upcoming||[]).filter(r=>r.sport===st.sport&&Date.parse(r.kickoff)>Date.now());
 const rows=up.map(r=>{const p=r.pressure,q=r.qualification,n=r.need,pk=r.need_pick,pub=q.public_side;
  const fails=Object.entries(q.checks).filter(([k,c])=>!c.pass).map(([k])=>CHECK[k]);const nm=sd=>sd==="home"?r.home:r.away;
  return el("div",{class:"game",style:"padding:8px 0;border-bottom:1px solid var(--line)",on:{click:()=>openGame(r.event_id)}},
   el("div",{},el("b",{},r.away+" @ "+r.home)," ",r.alignment==="ALIGNED"?badge("ALIGNED","INFO"):r.alignment==="DISAGREE"?badge("DISAGREE","LOW"):badge(r.alignment,"INFO")," ",q.qualified?badge("QUALIFIED","WATCH"):null),
   el("div",{class:"mute small"},ko(r)+" · snapshot "+local(r.taken_at)),
   r.need_stability?stability(r.need_stability,nm):null,
   r.ledger_basis&&r.ledger_basis.reconstructed_spread_wagers?el("div",{class:"small mute"},r.ledger_basis.reconstructed_spread_wagers+" of "+(r.ledger_basis.reconstructed_spread_wagers+r.ledger_basis.prospective_spread_wagers)+" spread wagers reconstructed from historical observed odds (run opened "+local(r.ledger_basis.run_opened_at)+")"):null,
   el("div",{class:"small"},"Book needs: ",n.side?el("b",{},nm(n.side)+" "+ln(pk.line)+" "+am(pk.price)):"balanced",pk.price_book?" ("+pk.price_book+")":""," · liability "+money(n.liability)+" · swing "+money(n.swing)),
   el("div",{class:"small"},"Recreational pressure: "+(pub?nm(pub)+" "+pct(p.sides[pub].rec_handle_share)+" of rec handle, "+pct(p.sides[pub].rec_ticket_share)+" of rec tickets":"–")+" · sharp "+pct(p.sharp_handle_share)+" of handle"),
   el("div",{class:"small mute"},"Spread handle "+money(p.handle)+" (rank "+r.week_handle_rank+" of "+r.week_games+" this week) · "+p.wagers+" wagers from "+p.bettors+" bettors · largest bettor "+pct(p.largest_bettor_share)),
   q.qualified?(r.research_pick?el("div",{class:"small"},el("b",{},"Research pick (paper): "+nm(r.research_pick))):el("div",{class:"small"},"Qualified, but the book's Need is on the public side: flagged, no pick")):
    el("div",{class:"small mute"},"Not qualified: "+fails.join(", ")),
   r.cohorts?el("div",{class:"small"},"Cohorts: "+(Object.entries(r.cohorts.evaluation).filter(([k,v])=>v.eligible).map(([k,v])=>k.split("_")[0]+" "+nm(v.pick_side)).join(" · ")||"none")):el("div",{class:"small mute"},"Snapshot predates cohort registration"))});
 const engines=Object.entries(T.reports_by_engine||{}).sort(([a],[b])=>(b===T.active_engine)-(a===T.active_engine));
 const section=([ev,rep])=>{const dis=rep.qualified_disagreements||{};
  const graded=(rep.rows||[]).filter(x=>x.sport===st.sport).slice(-40).reverse();
  return[card(el("h2",{},"Record: engine "+ev+(ev===T.active_engine?" (active)":" (separate record)")),
    el("p",{class:"small mute"},"Each sportsbook engine has its own record. Results are never combined across engines.")),
   cohortCard(rep.cohorts),
   card(el("h3",{},"All Need sides"),sumLine(rep.all_need)),
   card(el("h3",{},"Research subset: qualified and aligned"),sumLine(rep.research_subset)),
   card(el("h3",{},"Qualified but the Need disagrees with fading the public"),el("p",{class:"small"},dis.games+" game(s). "+(dis.note||"")),sumLine(dis.need_side)),
   card(brkTable(rep.by_largest_bettor_share,"All Need sides by largest-bettor share"),brkTable(rep.by_week_handle_percentile,"By weekly handle percentile"),brkTable(rep.by_sport,"By sport")),
   card(el("h3",{},"Sealed decisions ("+(st.sport==="nfl"?"NFL":"CFB")+")"),
    graded.length?el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Game"),el("th",{},"Need pick"),el("th",{},"Status"),el("th",{},"Result"))),
     el("tbody",{},graded.map(x=>el("tr",{},el("td",{},x.away+" @ "+x.home,el("div",{class:"mute small"},ko(x))),
      el("td",{class:"small"},x.need_side?((x.need_side==="home"?x.home:x.away)+" "+ln(x.line)+" "+am(x.price)):"–",el("div",{class:"mute"},(x.qualified?"qualified · ":"")+(x.alignment||""))),
      el("td",{class:"small"},x.status,x.decision_status!=="OK"?el("div",{class:"mute"},x.decision_status):null),
      el("td",{class:"small"},x.status==="GRADED"?(x.away_score+"–"+x.home_score+" (away–home): "+x.ats+(x.units!=null?", "+(x.units>=0?"+":"")+x.units.toFixed(2)+"u":"")):"–")))))):el("p",{class:"mute"},"No sealed decisions yet."))]};
 return[head,card(el("h3",{},"Upcoming (final 24 h): latest pre-kickoff snapshot"),
   el("p",{class:"small mute"},"Snapshots are saved immutably before kickoff; after kickoff the last one is sealed as the game's decision. "+T.snapshots_this_build+" snapshot(s) this build, "+T.decisions_total+" decisions sealed so far (all engines). Results: "+T.results_matched+" matched final scores from "+T.results_files+" file(s)"+(T.corrections?", "+T.corrections+" corrected":"")+"."),
   rows.length?el("div",{},rows):el("p",{class:"mute"},"No games within 24 h of kickoff.")),
  ...(engines.length?engines.flatMap(section):[card(el("p",{class:"mute"},"No sealed decisions yet."))])]};
const SPC={SHARP_VS_PUBLIC:"WATCH",ALIGNED:"OK",MIXED:"INFO",INSUFFICIENT:"INFO"};
const spSide=(g,bm,sd)=>bm==="total"?sd:(sd==="home"?g.home:g.away);
const spPct=x=>x==null?"–":x.toFixed(1)+"%";
function spBadge(x){return x&&x.status==="OK"?badge(x.display,SPC[x.classification]):badge("NO WAGERS","INFO")}
const spVal=(bm,sd,l)=>bm==="total"?(sd==="over"?-l:l):l;
const spNum=(g,bm,sd,l)=>bm==="total"?(sd==="over"?"Over ":"Under ")+l:bm==="spread"?spSide(g,bm,sd)+" "+(l===0?"PK":ln(l)):spSide(g,bm,sd);
const spDec=a=>a>0?1+a/100:1+100/Math.abs(a);
function spNow(g,bm,sd){const b=(S().games||[]).find(x=>x.event_id===g.event_id);const m=b&&b.markets&&b.markets[BMK[bm]];
 if(!m||m.status!=="OK")return null;const cons=bm==="spread"?(sd==="home"?m.line:-m.line):bm==="total"?m.line:null;
 let top=null;for(const[k,q]of Object.entries(m.books||{})){const v=q&&q.fresh&&q[sd];if(!v||v[0]==null)continue;
  const l=bm==="moneyline"?null:v[1];if(bm!=="moneyline"&&l==null)continue;
  const key=[bm==="moneyline"?0:spVal(bm,sd,l),spDec(v[0])];
  if(!top||key[0]>top.key[0]||(key[0]===top.key[0]&&key[1]>top.key[1]))top={key,book:k,line:l,price:v[0]}}
 return{line:cons,best:(m.best||{})[sd],top,as_of:b.as_of||S().observed_at}}
function spGot(g,bm,sd,b){if(!b)return"–";if(bm==="moneyline")return"last "+am(b.last_price)+" ("+local(b.last_at)+")";
 const rng=b.min_line===b.max_line?spNum(g,bm,sd,b.min_line):spNum(g,bm,sd,b.min_line)+" to "+(bm==="spread"?ln(b.max_line):b.max_line);
 return rng+" · last "+(bm==="spread"?(b.last_line===0?"PK":ln(b.last_line)):b.last_line)+" "+am(b.last_price)+" ("+local(b.last_at)+")"}
function spMove(g,bm,sd,b,n){if(!b||!n)return"";if(bm==="moneyline"){if(!n.best)return"";const d=spDec(n.best.price)-spDec(b.last_price);
  return Math.abs(d)<1e-9?"same price as the sharps' last bet":d<0?"price now WORSE than the sharps' last bet":"price now better than the sharps' last bet"}
 if(n.line==null||b.avg_line==null)return"";const d=Math.round((spVal(bm,sd,n.line)-spVal(bm,sd,b.avg_line))*10)/10;
 return Math.abs(d)<0.05?"same number the sharps got (on average)":d<0?"number now "+Math.abs(d)+" pt WORSE than the sharps got (on average)":"number now "+d+" pt better than the sharps got (on average)"}
function spNowTxt(g,bm,sd,n){if(!n)return"no current price";const p=[];
 if(bm!=="moneyline"&&n.line!=null)p.push("market "+spNum(g,bm,sd,n.line)+(n.best?" · best "+am(n.best.price)+" ("+n.best.book+")":""));
 else if(n.best)p.push(spSide(g,bm,sd)+" best "+am(n.best.price)+" ("+n.best.book+")");
 if(bm!=="moneyline"&&n.top&&n.top.line!==n.line)p.push("best number "+spNum(g,bm,sd,n.top.line)+" "+am(n.top.price)+" ("+n.top.book+")");
 return p.join(" · ")}
function spTable(g,bm,x){const sides=bm==="total"?["over","under"]:["away","home"];const R_=x.recreational,S_=x.sharp;
 const row=(lbl,f)=>el("tr",{},el("td",{class:"small"},lbl),...sides.map(sd=>el("td",{class:"num small"},f(sd))));
 return el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},""),...sides.map(sd=>el("th",{class:"num"},spSide(g,bm,sd))))),
  el("tbody",{},row("Rec handle % (of rec handle)",sd=>spPct(R_.sides[sd].handle_pct)),row("Sharp handle % (of sharp handle)",sd=>spPct(S_.sides[sd].handle_pct)),
   row("Rec ticket %",sd=>spPct(R_.sides[sd].ticket_pct)),row("Sharp ticket %",sd=>spPct(S_.sides[sd].ticket_pct)),
   row("Rec $ / tickets",sd=>money(R_.sides[sd].handle)+" / "+R_.sides[sd].tickets),row("Sharp $ / tickets",sd=>money(S_.sides[sd].handle)+" / "+S_.sides[sd].tickets),
   row("Sharps got",sd=>spGot(g,bm,sd,S_.sides[sd].bet_at)),row("Public got",sd=>spGot(g,bm,sd,R_.sides[sd].bet_at)),
   row("Now (last odds collected)",sd=>spNowTxt(g,bm,sd,spNow(g,bm,sd))),
   row("Move since sharps bet",sd=>spMove(g,bm,sd,S_.sides[sd].bet_at,spNow(g,bm,sd))||"–"))))}
function spDetail(g,bm,x){if(!x||x.status!=="OK")return el("div",{},el("b",{},MK[BMK[bm]]," "),spBadge(x));
 return el("div",{},el("p",{},el("b",{},MK[BMK[bm]]," "),spBadge(x)," ",el("span",{class:"small"},"Sharp handle "+spPct(x.sharp_share_of_handle_pct)+" of total "+money(x.total_handle)+" ("+x.total_tickets+" tickets; "+x.sharp.bettors+" sharp, "+x.recreational.bettors+" recreational customers)")),
  x.reasons.length?el("p",{class:"small mute"},x.reasons.join("; ")):null,spTable(g,bm,x))}
const SP_NOTE="Public = this book's SIMULATED recreational customers, not real sportsbook betting splits. From accepted wagers in the immutable ledger; each percentage uses its own group's handle or tickets in that game and market. SHARP VS PUBLIC needs at least 3 sharp wagers from 2+ sharp customers, no single sharp customer above 70% of sharp handle, sharp handle at least 5% of the market, 20+ recreational wagers, and a 55%+ handle majority in each group. The Need is computed from the book's modeled P&L and is independent of this label. Sharps got = the numbers the simulated sharps actually accepted (range, and their latest bet with its time). Now = the market at the last odds collection (see the age at the top); best number = the best line any tracked book offered for that side. Lines move: check the current number at your book before acting.";
function sfRec(r){if(!r)return"0-0";const n=r.W+r.L;return r.W+"-"+r.L+(r.P?"-"+r.P:"")+(n?" ("+Math.round(100*r.W/n)+"%)":"")}
function sharpFollowCard(){const F=D.sharp_follow;const sp=st.sport;
 const head=el("h2",{},badge("EXPERIMENTAL"),"Sharp follow list (simulated sharps)");
 if(!F||F.status!=="OK")return card(head,el("p",{class:"mute"},"Unavailable"+(F&&F.reason?": "+F.reason:"")+"."));
 const L=F.labels,R_=(F.records||{})[sp]||{},MKN={spread:"Spread",total:"Total"};
 const intro=el("p",{class:"small"},badge("NO BET")," For every spread and total the simulated sharps bet: the side with more sharp dollars, tagged with the patterns reviewed after week 1. Records are graded at the market line last observed before kickoff. ",el("b",{},"Before "+local(F.registered_at)+" the patterns were chosen after seeing the results (in-sample, flattering); only games after that test them."),
  " Simulated sharps, not real bettors. Not official plays.");
 const recRows=[];for(const m of["total","spread"])for(const t of F.tags){const r=(R_[m]||{})[t];if(!r)continue;
  recRows.push(el("tr",{},el("td",{class:"small"},L[t]),el("td",{class:"small"},MKN[m]),el("td",{class:"num small"},sfRec(r.before)),el("td",{class:"num small"},el("b",{},sfRec(r.forward)))))}
 const recTable=recRows.length?el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Pattern"),el("th",{},"Market"),el("th",{class:"num"},"Before (in-sample)"),el("th",{class:"num"},"Since registration"))),el("tbody",{},recRows))):el("p",{class:"mute"},"No graded games yet for this sport.");
 const bks=(S().book||{}).games||[];const f=st.f;
 const up=(F.upcoming||[]).filter(r=>r.sport===sp&&upcoming(r)&&gameMatch(r)&&(!f.market||BMK[r.market]===f.market));
 const items=up.map(r=>{const g=bks.find(x=>x.event_id===r.event_id)||r;const x=(g.sharp_public||{})[r.market];const b=x&&x.sharp&&x.sharp.sides[r.side]?x.sharp.sides[r.side].bet_at:null;
  const n=spNow(g,r.market,r.side);const sides=r.market==="total"?["over","under"]:["away","home"];
  const nm=s=>r.market==="total"?(s==="over"?"Over":"Under"):(s==="home"?r.home:r.away);
  const follow=n&&n.line!=null?spNum(g,r.market,r.side,n.line):nm(r.side);
  const recs=r.tags.map(t=>{const q=(R_[r.market]||{})[t];return L[t]+": "+(q?sfRec(q.before)+" before / "+sfRec(q.forward)+" since":"no record")});
  const mv=spMove(g,r.market,r.side,b,n);
  return el("div",{class:"card game",style:"margin:8px 0;cursor:pointer",on:{click:()=>openGame(r.event_id)}},
   el("div",{class:"small mute"},r.away+" @ "+r.home+" · "+ko(r)),
   el("div",{},el("b",{},MKN[r.market]+": follow "+follow)," ",...r.tags.filter(t=>t!=="ANY").map(t=>badge(L[t],"WATCH"))),
   el("div",{class:"small"},"Sharp $: "+sides.map(s=>nm(s)+" "+money(r.sharp_handle[s])+" ("+r.sharp_tickets[s]+" bets)").join(" / ")+" · "+r.sharp_bettors+" sharps · "+Math.round(100*r.sharp_share_on_side)+"% on the followed side"),
   el("div",{class:"small"},el("b",{},"Sharps got: "),spGot(g,r.market,r.side,b)),
   el("div",{class:"small"},el("b",{},"Now: "),spNowTxt(g,r.market,r.side,n)),mv?el("div",{class:"small"},el("b",{},mv)):null,
   el("div",{class:"small mute"},"Pattern records: "+recs.join(" · ")))});
 const done=(F.graded||[]).filter(r=>r.sport===sp).slice().reverse();
 const gr=el("details",{},el("summary",{},"Graded games ("+done.length+" shown of "+F.graded_total+" across sports)"),
  el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Game"),el("th",{},"Followed"),el("th",{},"Tags"),el("th",{},"Result"))),
   el("tbody",{},done.map(r=>{const nm=r.market==="total"?(r.side==="over"?"Over ":"Under ")+r.line:(r.side==="home"?r.home:r.away)+" "+(r.line==null?"":(r.line===0?"PK":ln(r.line)));
    return el("tr",{},el("td",{class:"small"},r.away+" @ "+r.home+" ("+(r.period==="forward"?"since":"before")+")"),el("td",{class:"small"},MKN[r.market]+": "+nm),
     el("td",{class:"small"},r.tags.filter(t=>t!=="ANY").map(t=>L[t]).join(", ")),el("td",{class:"small"},r.result?(r.result+" "+r.away_score+"-"+r.home_score):"no observed line"))})))));
 return card(head,intro,el("h3",{},"Pattern records ("+(sp==="cfb"?"CFB":"NFL")+")"),recTable,
  el("h3",{},"Upcoming plays fitting a pattern ("+up.length+")"),up.length?el("div",{},items):el("p",{class:"mute"},"None right now."),gr,
  el("p",{class:"small mute"},F.basis+". "+F.version+"."))}
function sharpPublicCard(){const gs=bookGames().filter(g=>g.sharp_public);const f=st.f;const ms=f.market?[Object.keys(BMK).find(k=>BMK[k]===f.market)]:["spread","total","moneyline"];
 const rows=[];for(const g of gs)for(const bm of ms){const x=g.sharp_public[bm];if(!x)continue;
  const sides=bm==="total"?["over","under"]:["away","home"];const ok=x.status==="OK";
  const maj=gr=>ok&&x[gr].majority_side?spSide(g,bm,x[gr].majority_side)+" "+spPct(x[gr].sides[x[gr].majority_side].handle_pct):"–";
  rows.push(el("tr",{class:"game",on:{click:()=>openGame(g.event_id)}},el("td",{},g.away+" @ "+g.home),el("td",{},MK[BMK[bm]]),el("td",{},spBadge(x)),
   el("td",{class:"small"},maj("recreational")),el("td",{class:"small"},maj("sharp")),
el("td",{class:"num small"},ok?spPct(x.sharp_share_of_handle_pct):"–"),
   el("td",{class:"num small hide-s"},ok?money(x.sharp.handle)+" / "+x.sharp.tickets:"–"),el("td",{class:"num small hide-s"},ok?money(x.recreational.handle)+" / "+x.recreational.tickets:"–")));
  const sd=ok&&x.sharp.majority_side;if(sd){const b=x.sharp.sides[sd].bet_at,n=spNow(g,bm,sd),mv=spMove(g,bm,sd,b,n);
   rows.push(el("tr",{class:"game sp-lines",on:{click:()=>openGame(g.event_id)}},el("td",{colspan:"8",class:"small"},el("div",{style:"position:sticky;left:0;max-width:calc(100vw - 80px);white-space:normal"},
    el("div",{},el("b",{},"Sharps got: "),spGot(g,bm,sd,b)),el("div",{},el("b",{},"Now: "),spNowTxt(g,bm,sd,n)),mv?el("div",{},el("b",{},mv)):null))))}}
 const vs=rows.length?gs.reduce((n,g)=>n+ms.filter(bm=>(g.sharp_public[bm]||{}).classification==="SHARP_VS_PUBLIC").length,0):0;
 return card(el("h2",{},badge("MODELED"),"Sharp vs public (simulated customers)"),el("p",{class:"small"},SP_NOTE),
  el("p",{},el("b",{},vs)," of "+rows.length+" game-markets: SHARP VS PUBLIC. Click a game for every side's percentages, dollars and tickets."),
  rows.length?el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Game"),el("th",{},"Market"),el("th",{},"Signal"),el("th",{},"Rec majority (of rec handle)"),el("th",{},"Sharp majority (of sharp handle)"),el("th",{class:"num"},"Sharp % of handle"),el("th",{class:"num hide-s"},"Sharp $ / tickets"),el("th",{class:"num hide-s"},"Rec $ / tickets"))),el("tbody",{},rows))):el("p",{class:"mute"},"No modeled wagers on upcoming games."))}
const OFFB={OFFICIAL:"OK",WATCHLIST:"WATCH",PASS:"INFO",EARLY:"INFO",NO_FINAL_EVALUATION:"STALE"};
const OFS={"official_needs.spread.v3":"Spreads","official_needs.total.v2":"Totals","official_needs.spread.v2":"Spreads (v2, retired: Public Pressure 65%)","official_needs.total.v1":"Totals (v1, retired: Public Pressure 65%)","official_needs.v1":"Spreads (v1, superseded)"};
const ofKpi=(v,t)=>el("div",{class:"kpi"},el("b",{},String(v)),t);
function ofPick(g,mk){const p=g.pick||{};if(!p.side)return"no Need side";
 const sel=(mk==="total"?String(p.side).toUpperCase()+" "+(p.line==null?"–":p.line):(p.team||p.side)+" "+ln(p.line));
 return sel+" "+am(p.price)+(p.price_book?" ("+p.price_book+")":"")}
function ofRules(g){return el("table",{},el("tbody",{},(g.rules||[]).map(r=>el("tr",{},el("td",{},r.pass?"✓":"✗"),el("td",{class:"small"},r.name+(r.mandatory?" (required)":"")),el("td",{class:"small mute"},r.value||"")))))}
function ofTier(g){if(g.status!=="OFFICIAL")return null;const f=(g.rules||[]).filter(r=>!r.pass).map(r=>r.name);
 return g.tier==="PERFECT"?badge("PERFECT 6/6","OK"):el("span",{},badge("STANDARD 5/6","WATCH"),el("span",{class:"small"}," failed: "+f.join(", ")))}
function ofCard(g,mk){return card(el("h3",{},badge(g.status==="EARLY"?"EARLY · indicates "+g.classification:g.status,OFFB[g.status]),g.status==="OFFICIAL"?badge("EXPERIMENTAL MODEL SIGNAL","WATCH"):null," "+g.away+" @ "+g.home),
 g.status==="OFFICIAL"?el("p",{},ofTier(g)):null,
 el("p",{},el("b",{},(mk==="total"?"Total Need: ":"Spread Need: ")+ofPick(g,mk))," · "+(g.score||"")+" rules · kickoff "+ko(g)),
 el("p",{class:"small mute"},g.frozen?"Frozen in an immutable pre-kickoff record this build; the last record before kickoff decides.":(g.status==="EARLY"?"More than 24 hours before kickoff: provisional indication only, nothing frozen.":"Not frozen.")),ofRules(g))}
function ofMarket(st,X){const mk=X.market,gs=X.games||[],n=k=>gs.filter(g=>g.status===k);const off=n("OFFICIAL"),wl=n("WATCHLIST"),ps=n("PASS"),ea=n("EARLY");
 const out=[card(el("h2",{},(mk==="total"?"Totals":"Spreads")+" · "+st),el("div",{class:"grid"},ofKpi(off.length,"Official ("+off.filter(g=>g.tier==="PERFECT").length+" perfect, "+off.filter(g=>g.tier==="STANDARD").length+" standard)"),ofKpi(wl.length,"Watchlist"),ofKpi(ps.length,"Pass"),ofKpi(ea.length,"more than 24 h away")))];
 out.push(...(off.length?off.map(g=>ofCard(g,mk)):[card(el("p",{},el("b",{},"0 Official "+(mk==="total"?"total":"spread")+" Needs")," on this slate right now."))]));
 if(wl.length)out.push(card(el("h3",{},"Watchlist ("+wl.length+")"),el("p",{class:"small mute"},"Both required rules and 2 of the other 4: not official.")),...wl.map(g=>ofCard(g,mk)));
 if(ps.length||ea.length)out.push(card(el("details",{},el("summary",{},"Pass ("+ps.length+") and more than 24 hours away ("+ea.length+")"),...[...ps,...ea].map(g=>el("div",{},el("p",{},badge(g.status==="EARLY"?"EARLY":"PASS","INFO")," "+g.away+" @ "+g.home+" · "+ko(g)+" · "+ofPick(g,mk)+" · "+(g.score||"")),el("p",{class:"small mute"},g.failed&&g.failed.length?"Fails: "+g.failed.join("; "):""))))));
 return out}
function ofRecord(ON,sp){const RS=(ON||{}).reports;if(!RS)return card(el("p",{},badge(((ON||{}).status)||"UNAVAILABLE","UNAVAILABLE")," "+(((ON||{}).reason)||"No Official Needs records yet.")));
 const line=x=>x.graded?x.wins+"-"+x.losses+"-"+x.pushes+", "+(x.units>=0?"+":"")+x.units.toFixed(2)+" units, ROI "+(x.roi_pct>=0?"+":"")+x.roi_pct.toFixed(1)+"%":"no graded selections yet";
 const keys=Object.keys(RS);if(!keys.length)return card(el("h3",{},"Record of sealed Official Needs ",badge("EXPERIMENTAL MODEL SIGNALS","WATCH")),el("p",{class:"mute"},"No sealed selections yet."));
 return card(el("h3",{},"Record of sealed Official Needs ("+(sp==="nfl"?"NFL":"CFB")+") ",badge("EXPERIMENTAL MODEL SIGNALS","WATCH")),...keys.map(k=>{const R=RS[k],o=R.official.by_sport[sp]||{},w=R.watchlist.by_sport[sp]||{};const rows=R.official.rows.filter(r=>r.sport===sp);
  const dq=(R.data_quality_issues||[]).filter(x=>x.sport===sp);
  return el("div",{},el("h3",{},(OFS[k]||k)+" · "+k),el("p",{},el("b",{},(o.selections||0)+" official: "),line(o)+(o.pending?" · "+o.pending+" awaiting result":"")),
   el("p",{class:"small mute"},"Watchlist (not official): "+(w.selections||0)+" sealed, "+line(w)+". "+(o.warning||"")+" Graded at the frozen line and price; 1 unit each; separate from C1–C4."),
   dq.length?el("p",{class:"small"},badge("DATA QUALITY","STALE")," "+dq.map(x=>x.away+" @ "+x.home+": sealed "+x.classification+" ("+x.data_quality+")").join(" · ")):null,
   rows.length?el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},...["Game","Selection","Qualification","Result"].map(h=>el("th",{},h)))),el("tbody",{},rows.map(r=>el("tr",{},el("td",{},r.away+" @ "+r.home,el("div",{class:"mute small"},ko(r))),
    el("td",{class:"small"},(r.market==="total"?String(r.side).toUpperCase()+" "+r.line:(r.team||r.side||"–")+" "+ln(r.line))+" "+am(r.price)+(r.price_book?" ("+r.price_book+")":"")),
    el("td",{class:"small mute"},(r.tier?r.tier+" "+(r.score||""):"v1 6/6")+(r.tier==="STANDARD"&&r.failed.length?" · failed: "+r.failed.join(", "):"")+" · "+r.minutes_before_kickoff+" min before kickoff"),
    el("td",{class:"small"},r.status==="GRADED"?r.away_score+"–"+r.home_score+" (away–home): "+r.ats+", "+(r.units>=0?"+":"")+r.units.toFixed(2)+"u":r.status)))))):null)}))}
R.official=()=>{const bk=S().book||{},O=bk.official,ON=D.official_needs||{},sp=st.sport;
 const out=[card(el("h2",{},badge("EXPERIMENTAL","WATCH")," Official ArbitrEdge Needs · "+(sp==="nfl"?"NFL":"CFB Top-25")),
  el("p",{class:"small"},(O&&O.label)||ON.label||""),
  el("p",{class:"small mute"},"Strategies official_needs.spread.v3 and official_needs.total.v2, each applied to its own market with its own handle and liability rankings (Public Pressure: at least 60% of recreational handle and a recreational ticket majority against the Need). OFFICIAL needs both required rules (stable Need, valid data) plus at least 3 of High Handle, Public Pressure, Sharp Alignment and High Liability: 6/6 is PERFECT, 5/6 is STANDARD (the failed rule is shown). WATCHLIST: both required rules plus 2 of the 4. Within 24 hours of kickoff each evaluation is frozen; the last one before kickoff decides and must be from the final 3 hours, otherwise the game is sealed NO FINAL EVALUATION and is never official. Graded at the frozen line and price; never created after the fact. Research thresholds, not proven profitable; the site's default decision remains NO BET."+(O&&O.superseded?" Records made under "+O.superseded+" are kept, sealed and graded under their own rules.":"")))];
 if(!O||!O.strategies)out.push(card(el("p",{},badge("UNAVAILABLE","UNAVAILABLE")," No Official Needs evaluation in this build.")));
 else{out.push(card(el("p",{class:"small mute"},"Evaluated "+local(O.evaluated_at)+" from the ledger as of "+local(O.ledger_time)+". Strategies registered "+local(O.registered_at)+".")));
  for(const[st,X]of Object.entries(O.strategies))out.push(...ofMarket(st,X))}
 out.push(ofRecord(ON,sp));return out};
R.sharp=()=>{const f=st.f;const rows=(S().sharp||[]).filter(r=>cfbGate(r.top25)&&(!f.market||r.market===f.market)&&upcoming(r)&&gameMatch(r));
 const ev=rows.filter(r=>r.status!=="INSUFFICIENT EVIDENCE");
 return[freshBanner(),cfbBanner(),sharpFollowCard(),sharpPublicCard(),card(el("h2",{},badge("EXPERIMENTAL"),"Sharp money (price movement)"),
  el("p",{class:"small"},"Evidence from real price movement at market-making books versus retail books. Reverse line movement and ticket/handle divergence need real betting splits and are UNAVAILABLE. The score is uncalibrated; when the quote history is too thin the page says INSUFFICIENT EVIDENCE instead of guessing."),
  filters({market:1}),el("p",{},el("b",{},ev.length)," of "+rows.length+" game-markets show evidence."),
  el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Game"),el("th",{},"Market"),el("th",{},"Status"),el("th",{class:"hide-s"},"Detail"))),
   el("tbody",{},rows.map(r=>el("tr",{class:"game",on:{click:()=>openGame(r.event_id)}},el("td",{},r.away+" @ "+r.home),el("td",{},MK[r.market]),
    el("td",{},r.status==="INSUFFICIENT EVIDENCE"?badge("INSUFFICIENT EVIDENCE","INFO"):el("span",{},badge(r.status,"WATCH")," "+(r.suspected_side||""))),
    el("td",{class:"hide-s small mute"},(r.supporting||[]).slice(0,2).join("; ")||(r.reason||""),
     (r.timing_unresolved||[]).length?el("div",{},"Timing unresolved: "+r.timing_unresolved.join("; ")):null)))))))]};
R.ev=()=>{const f=st.f;const rows=(S().scanner||[]).filter(r=>cfbGate(r.top25)&&(!f.market||r.market===f.market)&&(!f.book||r.book===f.book)
  &&upcoming(r)&&gameMatch(r)&&(r.ev_interval?r.ev_interval[0]:0)>=parseFloat(f.strength));
 const stale=freshness(S().observed_at)!=="LIVE";
 return[freshBanner(),cfbBanner(),card(el("h2",{},"EV scanner"),el("p",{class:"small"},badge("WATCH")," Estimated value, separate from the simulated sportsbook's Need. Prices that beat the market-maker consensus after vig removal (least favourable of three methods) and model uncertainty. The strategy is NOT validated: these are paper candidates, never official plays."+(stale?" Prices are not live: re-check before acting on anything.":"")),
  filters({market:1,book:1,strength:1}),
  el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Game"),el("th",{},"Bet"),el("th",{},"Book"),el("th",{class:"num"},"Price"),el("th",{class:"num"},"EV (range)"))),
   el("tbody",{},rows.map(r=>el("tr",{class:"game"+(stale?" dim":""),on:{click:()=>openGame(r.game_id)}},el("td",{},r.away+" @ "+r.home,el("div",{class:"mute small"},ko(r))),
    el("td",{},MK[r.market]+" "+r.selection+(r.line!=null?" "+ln(r.line):"")),el("td",{},r.book),el("td",{class:"num"},am(r.american)),
    el("td",{class:"num"},pct(r.ev_per_unit),el("div",{class:"mute small"},r.ev_interval?pct(r.ev_interval[0])+"–"+pct(r.ev_interval[1]):""))))))),
  rows.length?null:el("p",{class:"mute"},"No candidates match."))]};
R.sims=()=>[card(el("h2",{},"SIMS"),el("p",{},badge(D.sims.status,"UNAVAILABLE")," "+D.sims.detail))];
R.performance=()=>{const out=[];for(const[k,s]of Object.entries(D.sports)){const h=s.performance;if(!h){out.push(card(el("h3",{},s.label),el("p",{class:"mute"},"No analysis yet.")));continue}
 const brk=(o,t)=>Object.keys(o||{}).length?el("div",{},el("h3",{},t),el("table",{},el("tbody",{},Object.entries(o).map(([kk,v])=>el("tr",{},el("td",{},kk),el("td",{class:"num"},v?v.n+" opp / "+v.games+" games":"–"),el("td",{class:"num"},v?pct(v.mean)+" ("+pct(v.lo)+" to "+pct(v.hi)+")":"–")))))):null;
 out.push(card(el("h3",{},s.label),perfLine(h),el("p",{class:"small mute"},"Closing EV = the price taken, evaluated at the observed closing consensus. Opportunities are counted once, at first sighting; a game whose close was not observed is excluded, never filled in. Confidence intervals are clustered by game."),
  brk(h.by_book,"By book"),brk(h.by_market,"By market"),brk(h.by_hours_to_kickoff,"By hours to kickoff")))}
 out.push(card(el("h3",{},"Realized results"),el("p",{class:"mute"},"Graded results appear once final scores are ingested (NFL: nflverse). No strategy has passed the evidence gate.")));
 return[card(el("h2",{},"Performance center"),el("p",{class:"small"},"Proof standard: positive closing EV with a confidence interval above zero over enough independent games, then walk-forward and multiple-testing checks. Until then every strategy stays WATCH/PAPER.")),...out]};
function settleCard(){const X=D.settlement;if(!X)return null;
 if(X.status==="INTEGRITY_FAILED")return card(badge("INTEGRITY FAILED","UNAVAILABLE")," Settlement records failed verification: "+(X.reason||""));
 const sm=(X.summary||{})[st.sport]||{};const c=x=>x==null?"–":money(x/100);const rc=X.reconciliation||{};
 return card(el("h3",{},badge("MODELED"),"Settled simulated wagers and bankrolls"),
  el("p",{class:"small"},"Every accepted simulated wager is settled at its own line and price once its game has a final score (NFL: nflverse; CFB: CollegeFootballData). No score means the wager stays open; nothing is guessed. Corrections are added as new records; history is never overwritten. Bankrolls = opening bankroll + settled P&L − open stakes, in cents."),
  el("div",{class:"grid"},el("div",{class:"kpi"},el("b",{},(sm.settled_wagers||0).toLocaleString()),"settled wagers ("+(sm.open_wagers||0).toLocaleString()+" open)"),
   el("div",{class:"kpi"},el("b",{},c(sm.book_realized_pnl_cents)),"book realized P&L"+(sm.book_hold_pct!=null?" (hold "+sm.book_hold_pct+"%)":"")),
   el("div",{class:"kpi"},el("b",{},sm.results?sm.results.win+"-"+sm.results.loss+"-"+sm.results.push:"–"),"bettor W-L-P"),
   el("div",{class:"kpi"},el("b",{},X.status==="OK"?"exact":"CHECK"),"reconciliation ("+(rc.game_mismatch_cents||0)+"¢ / "+(rc.bettor_mismatch_cents||0)+"¢ off)")),
  el("table",{class:"small"},el("tbody",{},Object.entries(sm.by_archetype||{}).map(([a,v])=>el("tr",{},el("td",{},a),el("td",{class:"num"},v.bettors+" bettors"),
   el("td",{class:"num"},v.up+" up · "+v.down+" down · "+v.even+" even"),el("td",{class:"num"},c(v.pnl_cents)))))),
  el("p",{class:"small mute"},X.settlement_rows+" settlement records ("+X.corrections+" corrections); "+X.open_overdue_48h+" open wagers more than 48 h past kickoff; "+(sm.bettors_below_min_stake||0)+" bettors below the minimum stake. Simulated customers: not a betting result."))}
function behaviourCard(){const bk=S().book||{},m=bk.behaviour;if(!m)return null;const p=m.participation;
 const row=(k,v)=>el("tr",{},el("td",{},k),el("td",{class:"num"},v));
 return card(el("h3",{},badge("MODELED"),"Customer behaviour this week ("+m.engine_version+")"),
  el("p",{class:"small mute"},"How the simulated customers actually behaved: an output of individual decisions, not a target. Not observed data."),
  el("table",{class:"small"},el("tbody",{},
   p?row("Customers active / sitting out / busted",p.active_this_week.toLocaleString()+" / "+p.sitting_out.toLocaleString()+" / "+p.busted.toLocaleString()):null,
   row("Customers who bet",m.customers_with_wagers.toLocaleString()+" of "+m.customers.toLocaleString()),
   row("Wagers per betting customer (median / p90 / max)",m.wagers_per_betting_customer.median+" / "+m.wagers_per_betting_customer.p90+" / "+m.wagers_per_betting_customer.max),
   row("Handle held by top 1% / top 10% of customers",pct(m.concentration.top_1pct_customers_share)+" / "+pct(m.concentration.top_10pct_customers_share)),
   row("Weekly stake ÷ bankroll (median / p90)",pct(m.weekly_turnover_of_bankroll.median)+" / "+pct(m.weekly_turnover_of_bankroll.p90)),
   row("Market mix (spread / total / moneyline)",pct(m.market_mix.spread)+" / "+pct(m.market_mix.total)+" / "+pct(m.market_mix.moneyline)),
   row("Arrivals that passed because the price was too expensive",pct(m.price_sensitivity_pass_share)))),
  el("table",{class:"small"},el("tbody",{},Object.entries(m.stake_by_group).map(([g,v])=>row(g,v.n+" wagers · median $"+v.median+" · p90 $"+v.p90+" · max $"+v.max)))),
  p?el("p",{class:"small mute"},"Active share by group: "+Object.entries(p.active_share_by_group).map(([g,v])=>g+" "+pct(v)).join(" · ")):null)}
R.health=()=>{const c=D.collection;return[settleCard(),behaviourCard(),card(el("h2",{},"Collection health"),
 el("table",{},el("tbody",{},el("tr",{},el("td",{},"V4 credits this month"),el("td",{class:"num"},c.v4_credits_this_month)),
  el("tr",{},el("td",{},"Requests this month"),el("td",{class:"num"},c.requests_this_month)),
  el("tr",{},el("td",{},"Provider credits remaining (shared with V3)"),el("td",{class:"num"},c.provider_remaining==null?"–":c.provider_remaining.toLocaleString())),
  el("tr",{},el("td",{},"Cost mismatches vs estimate"),el("td",{class:"num"},c.cost_mismatches)),
  el("tr",{},el("td",{},"Data anomalies"),el("td",{class:"num"},c.anomalies)))),
 el("h3",{},"Fetches"),el("table",{},el("tbody",{},(c.fetches||[]).map(x=>el("tr",{},el("td",{},x.sport),el("td",{class:"num"},x.fetches+" fetches"),el("td",{class:"small"},"last "+local(x.last)+" ("+fmtAge(ageMin(x.last))+" ago)"))))),
 el("p",{class:"small mute"},"Page built "+local(D.generated_at)+(D.commit?" from commit "+D.commit:"")+". Freshness labels are recomputed in your browser every minute.")),
 ...Object.entries(D.sports).map(([k,s])=>s.book&&s.book.audit?card(el("h3",{},badge("MODELED"),"Synthetic book audit: "+s.label),
  el("table",{class:"small"},el("tbody",{},...["slate_id","window","population_id","population_hash","params_hash","engine_version","run_id","bettors","bettors_with_wagers","wagers","wagers_loaded_from_ledger","new_wagers_this_build","ledger_intervals","ledger_files_loaded","replay_verified","replay_verified_at","ledger_hash","arrivals","advanced_to","data_complete_through","last_observation","market_source"].map(f=>el("tr",{},el("td",{},f),el("td",{style:"word-break:break-all"},String(s.book.audit[f])))))),(s.book.superseded_runs||[]).length?el("p",{class:"small mute"},"Superseded runs (earlier model versions, kept unchanged in the ledger, not shown): "+s.book.superseded_runs.map(r=>r.slate_id+" ("+r.engine_version+", "+r.wagers+" wagers)").join("; ")):null):null)]};
R.game=(id)=>{const g=gameById(id);if(!g)return[card(el("p",{},"Game not found (it may have kicked off)."),el("a",{href:"#odds"},"Back"))];
 const sp=Object.entries(D.sports).find(([k,s])=>(s.games||[]).includes(g));const s=sp[1];
 const bkg=((s.book||{}).games||[]).find(x=>x.event_id===id);
 const out=[el("p",{},el("a",{href:"#"+(st.prev||"odds")},"← Back")),card(el("h2",{},gameHead(g)),el("p",{class:"mute"},ko(g)+" · "+s.label+" · observed "+fmtAge(ageMin(s.observed_at))+" ago ",badge(freshness(s.observed_at))),
  g.rankings?el("p",{class:"small"},g.rankings.status==="OK"?"AP poll week "+g.rankings.poll_week+" ("+g.rankings.season_type+"), fetched "+local(new Date(g.rankings.fetched_at*1000).toISOString())+", source "+g.rankings.source:
   [badge(g.rankings.status==="PENDING"?"PENDING":"UNAVAILABLE",g.rankings.status==="PENDING"?"WATCH":"UNAVAILABLE")," "+(g.rankings.reason||""),
    g.rankings.provisional?el("span",{class:"mute"}," Provisional (current poll week "+g.rankings.provisional.poll_week+"): "+g.away+" "+(g.rankings.provisional.away_rank?"#"+g.rankings.provisional.away_rank:"NR")+", "+g.home+" "+(g.rankings.provisional.home_rank?"#"+g.rankings.provisional.home_rank:"NR")+"."):null]):null)];
 for(const m of["spreads","totals","h2h"]){const x=g.markets[m]||{};const sides=m==="totals"?["over","under"]:["home","away"];
  const rows=Object.entries(x.books||{}).map(([b,q])=>el("tr",{class:q.fresh?"":"dim"},el("td",{},b,(x.consensus_books||[]).includes(b)?el("span",{class:"mute small"}," (consensus)"):null),
   ...sides.map(sd=>el("td",{class:"num"},q[sd]?(m==="h2h"?"":ln(q[sd][1])+" ")+am(q[sd][0]):"–")),el("td",{class:"num small mute"},fmtAge(q.age_s/60))));
  out.push(card(el("h3",{},badge("OBSERVED"),MK[m]+(x.line!=null?" (consensus line "+(m==="spreads"?"home "+ln(x.line):x.line)+")":"")),
   x.status==="OK"?el("p",{class:"small"},"Fair: "+sides.map(sd=>sd+" "+pct(x.fair[sd])).join(", ")+" · basis "+x.basis):el("p",{class:"mute"},x.status),
   el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Book"),...sides.map(sd=>el("th",{class:"num"},sd)),el("th",{class:"num"},"quote age at observation"))),el("tbody",{},rows)))))}
 const scan=(s.scanner||[]).filter(r=>r.game_id===id);if(scan.length)out.push(card(el("h3",{},badge("WATCH"),"EV candidates"),...scan.map(r=>el("p",{class:"small"},MK[r.market]+" "+r.selection+(r.line!=null?" "+ln(r.line):"")+" at "+r.book+" "+am(r.american)+": EV "+pct(r.ev_per_unit)+" (range "+pct(r.ev_interval[0])+"–"+pct(r.ev_interval[1])+"), paper only"))));
 const sh=(s.sharp||[]).filter(r=>r.event_id===id);if(sh.length)out.push(card(el("h3",{},badge("EXPERIMENTAL"),"Sharp money"),...sh.map(r=>el("p",{class:"small"},MK[r.market]+": "+r.status+(r.suspected_side?" ("+r.suspected_side+")":"")))));
 if(bkg){const rows=Object.entries(bkg.splits).filter(([m,r])=>r.status==="OK");out.push(card(el("h3",{},badge("MODELED"),"Synthetic book on this game"),
  rows.length?rows.map(([bm,r])=>el("div",{},el("b",{},MK[BMK[bm]]," ",r.sample==="LOW"?badge("LOW SAMPLE","LOW"):null),...Object.entries(r.sides).map(([sd,v])=>el("div",{},el("span",{class:"small"},sd),splitBar(v,(bkg.bands[bm]||{})[sd]))))):el("p",{class:"mute"},"No modeled wagers yet."),
  bkg.need&&bkg.need.spread?el("p",{class:"small"},"Spread Need (liability): "+(bkg.need.spread.financial_need_side||"balanced")):null))}
 if(bkg&&bkg.sharp_public)out.push(card(el("h3",{},badge("MODELED"),"Sharp vs public (simulated customers)"),el("p",{class:"small mute"},SP_NOTE),...["spread","total","moneyline"].map(bm=>spDetail(g,bm,bkg.sharp_public[bm]))));
 return out};
R.mi=()=>{const M=D.market_intel||{};if(M.status!=="OK")return[card(el("h2",{},"Market Intelligence"),el("p",{class:"mute"},"Unavailable: "+(M.reason||M.status||"not built")))];
 const cs=(M.cards||{})[st.sport]||[];const pctx=v=>v==null?"–":Math.round(v)+"th pct";
 const need=x=>x&&x.need?el("b",{},x.decision+(x.strength?" ("+x.strength+")":"")):el("span",{class:"mute"},x?(x.decision||"–"):"–");
 const books=(bk,kind)=>el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Book"),el("th",{},"Response"),el("th",{},kind==="total"?"Total / price for public side":"Key number"))),
  el("tbody",{},...Object.entries(bk||{}).map(([b,v])=>el("tr",{},el("td",{},b),el("td",{},v.regime+(v.reason?" ("+v.reason+")":"")),
   el("td",{class:"small"},kind==="total"?(v.total_start!=null?v.total_start+"→"+v.total_end+", "+(v.price_start||"–")+"→"+(v.price_end||"–")+(v.key_total_state&&!/NO_KEY/.test(v.key_total_state)?" · "+v.key_total_state:""):"–"):(v.key||"–")))))));
 const head=card(el("h2",{},"Market Intelligence ",badge("EXPERIMENTAL")),el("p",{class:"small"},M.label+". Original ArbitrEdge spread engine (unchanged code) and the totals extension "+(cs[0]&&cs[0].total?cs[0].total.engine:"mi_totals.v1")+
  ". Public preference is an ESTIMATE from team form and attention, never ticket or handle data. Separate from the Synthetic Sportsbook's Financial NEED and from SIMS. Not a bet."),
  el("p",{class:"small mute"},"Evaluated at the build time for games kicking off within 30 h (the original starts at T-24h). Historical 2026 audit: no rule passed multiple-testing correction."));
 if(!cs.length)return[head,card(el("p",{class:"mute"},"No game in the evaluation window.")),agreeCard(M.agreement_research)];
 return[head,agreeCard(M.agreement_research),...cs.map(c=>{const sp=c.spread||{},tt=c.total||{};return card(el("h3",{},c.game),el("p",{class:"mute small"},"kickoff "+c.kickoff+" · as of "+c.as_of),
  el("div",{class:"mi2"},
   el("div",{},el("h3",{},"Spread market intelligence"),el("p",{class:"small"},"Market-derived NEED: ",need(sp)),
    el("p",{class:"small"},"Estimated public side: "+(sp.public_side_estimate||"–")+" ("+pctx(sp.public_magnitude_pct)+") · market activity "+pctx(sp.handle_intensity_pct)),
    el("p",{class:"small"},"Books: "+(sp.regime||"–")+(sp.breadth?" ("+sp.breadth+")":"")+" · segment "+(sp.segment||"–")+" · data quality "+(sp.data_quality||"–")),books(sp.books,"spread")),
   el("div",{},el("h3",{},"Totals market intelligence"),el("p",{class:"small"},"Market-derived NEED: ",need(tt)),
    el("p",{class:"small"},"Estimated public lean: "+(tt.public_lean_estimate||"–")+" ("+pctx(tt.public_magnitude_pct)+", inputs "+(tt.inputs||"–")+") · totals activity "+pctx(tt.activity_pct)),
    el("p",{class:"small"},"Books: "+(tt.regime||"–")+(tt.breadth?" ("+tt.breadth+")":"")+" · pro vs rec: "+(tt.sequencing||"–")),
    el("p",{class:"small mute"},tt.reason||""),books(tt.books,"total"))),
  el("div",{class:"mi2"},shp("Spread",sp,["home","away"]),shp("Totals",tt,["over","under"])))})]};
const AG={rule:"v3",cut:"T-1h",src:"genuine",market:"spread",cat:"",show:false};
const AGCAT={STRONG_AGREEMENT:"Strong agreement",MODERATE_AGREEMENT:"Moderate agreement",DISAGREEMENT:"Disagreement",NO_SHARP_CONSENSUS:"No sharp consensus",MARKET_NEED_ONLY:"NEED only (no sharp wagers)",SHARP_ONLY:"Sharps only (no NEED)",NO_SIGNAL:"No signal",ALL_NEED:"All NEED picks (any sharp position)"};
R.circa=()=>{const Cx=D.circa||{};const hd=card(el("h2",{},"Circa 99% ",badge("EXPERIMENTAL")),
  el("p",{},"Your buddy's engine, rebuilt with fixed rules: when one side has 99%+ of Circa's money, the default is to fade it. NORMAL tier (bets under 99%) is playable; THIN tier (bets also 99%+, likely one ticket) is logged only. Lean: LIKE = bet the fade; FLIP = bet the heavy side (line moved with the money and 50–67% of bets suggests one sharp ticket); PASS = no bet. Every sport on the sheet: games and DraftKings' opening and current lines come from ESPN at the moment you upload, final scores from ESPN."),
  el("p",{class:"small mute"},"How to add a sheet: save @BettingSplits' Circa image from X, then in GitHub open the circa_uploads folder → Add file → Upload files → choose the image → Commit. The site updates a few minutes later. Rule "+(Cx.rule||"")+". Grok's research calls can't be reproduced; these are written rules. Tracked picks, not official plays: default NO BET."));
 if(Cx.status!=="OK")return[hd,card(el("p",{class:"mute"},"Unavailable: "+(Cx.reason||Cx.status||"not built")))];
 const S=Cx.signals||[],now=Date.now()/1000;
 const tm=(r,side)=>{const[a,h]=r.game.split(" @ ");return side==="home"?h:side==="away"?a:side==="over"?"Over":"Under"};
 const lineTxt=(r,side)=>{const l=side===r.fade?r.line_fade:r.line_heavy;return r.market==="money"?tm(r,side)+" ML "+am(l):r.market==="spread"?tm(r,side)+" "+ln(l):tm(r,side)+" "+l};
 const pick=r=>r.lean==="LIKE"?lineTxt(r,r.fade):r.lean==="FLIP"?lineTxt(r,r.heavy):"–";
 const tally=(xs,k)=>{const g=xs.map(r=>r.graded&&r.graded[k]).filter(Boolean);const w=g.filter(x=>x.result==="W").length,l=g.filter(x=>x.result==="L").length,p=g.length-w-l,u=g.reduce((a,x)=>a+x.units,0);
  return g.length?w+"-"+l+(p?"-"+p:"")+" · "+(u>=0?"+":"")+u.toFixed(2)+"u":"–"};
 const plays=S.filter(r=>r.lean==="LIKE"||r.lean==="FLIP");
 const up=S.filter(r=>r.kickoff>now).sort((a,b)=>({LIKE:0,FLIP:1,PASS:2,LOG:3}[a.lean]-{LIKE:0,FLIP:1,PASS:2,LOG:3}[b.lean])||a.kickoff-b.kickoff);
 const sigRow=r=>el("tr",{},el("td",{class:"small"},local(r.kickoff_iso)),el("td",{},r.sport),el("td",{},r.game),el("td",{},r.market),
   el("td",{},lineTxt(r,r.heavy)+" ("+r.handle+"% $ / "+r.bets+"% bets)"),el("td",{},r.tier),el("td",{},el("b",{},r.lean)),el("td",{},el("b",{},pick(r))),el("td",{class:"small"},r.reason));
 const upCard=card(el("h3",{},"Signals for upcoming games ("+up.length+")"),up.length?el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Start"),el("th",{},"Sport"),el("th",{},"Game"),el("th",{},"Market"),el("th",{},"Heavy side (Circa)"),el("th",{},"Tier"),el("th",{},"Lean"),el("th",{},"Pick"),el("th",{},"Why"))),el("tbody",{},...up.map(sigRow)))):
  el("p",{class:"mute"},"No signals for upcoming games. Upload today's Circa sheet to check."));
 const grp=(t,xs)=>el("tr",{},el("td",{},t),el("td",{class:"num"},String(xs.length)),el("td",{},tally(xs,"lean")),el("td",{},tally(xs,"fade")),el("td",{},tally(xs,"follow")));
 const norm=S.filter(r=>r.tier==="NORMAL"),thin=S.filter(r=>r.tier==="THIN");
 const rec=card(el("h3",{},"Record (1 unit per pick)"),el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},""),el("th",{class:"num"},"Signals"),el("th",{},"Our lean"),el("th",{},"Fade the 99%"),el("th",{},"Follow the 99%"))),
  el("tbody",{},grp("All",S),...[...new Set(S.map(r=>r.sport))].sort().map(sp=>grp(sp,S.filter(r=>r.sport===sp))),grp("LIKE (fade picks)",S.filter(r=>r.lean==="LIKE")),grp("FLIP (follow picks)",S.filter(r=>r.lean==="FLIP")),grp("PASS",S.filter(r=>r.lean==="PASS")),
   grp("NORMAL tier",norm),grp("THIN tier",thin),grp("Spreads",S.filter(r=>r.market==="spread")),grp("Totals",S.filter(r=>r.market==="total")),grp("Moneylines",S.filter(r=>r.market==="money")),
   grp("Line moved AGAINST the money",S.filter(r=>r.movement==="AGAINST")),grp("Line moved WITH the money",S.filter(r=>r.movement==="WITH")),grp("Line held",S.filter(r=>r.movement==="NONE")),
   ...["<70","70-89","90-98","99+"].map(b=>grp("Bets "+b+"%",S.filter(r=>r.bucket===b)))))),
  el("p",{class:"small mute"},"Graded at Circa's number on the sheet: price = DraftKings' current price for that side when its number is the same, else -110; moneylines at the sheet's price. Final scores from ESPN."));
 const done=S.filter(r=>r.kickoff<=now).sort((a,b)=>b.kickoff-a.kickoff);
 const res=x=>!x?el("span",{class:"mute"},"–"):el("span",{class:x.result==="W"?"pos":x.result==="L"?"neg":""},x.result+" "+(x.units>=0?"+":"")+x.units.toFixed(2));
 const doneCard=card(el("h3",{},"Results ("+done.length+")"),done.length?el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Date"),el("th",{},"Game"),el("th",{},"Market"),el("th",{},"Lean"),el("th",{},"Pick"),el("th",{class:"num"},"Final (away–home)"),el("th",{},"Lean result"),el("th",{},"Fade"),el("th",{},"Follow"))),
  el("tbody",{},...done.map(r=>el("tr",{},el("td",{class:"small"},(r.kickoff_iso||"").slice(5,10)),el("td",{},r.sport+": "+r.game),el("td",{},r.market+" · "+r.tier),el("td",{},r.lean),el("td",{},pick(r)),el("td",{class:"num"},r.final?r.final[1]+"–"+r.final[0]:"awaiting"),
   el("td",{},res(r.graded&&r.graded.lean)),el("td",{},res(r.graded&&r.graded.fade)),el("td",{},res(r.graded&&r.graded.follow))))))):el("p",{class:"mute"},"No finished games yet."));
 const ups=Cx.uploads||[];
 const upl=el("details",{},el("summary",{},"Uploaded sheets ("+(Cx.n_uploads||0)+") and what was read"),...ups.map(u=>el("p",{class:"small"},el("b",{},u.image)," · uploaded "+local(u.uploaded_at_iso)+" · "+(u.error?"ERROR: "+u.error:(u.rows_read||0)+" games read, "+(u.games_ok||0)+" fully readable, "+(u.problems||0)+" with a number that couldn't be read (skipped, never guessed) · "+(u.signals||0)+" signals, "+(u.unmatched||0)+" 99% sides not matched to a game"))));
 return[hd,upCard,rec,doneCard,card(upl)]};
R.plays=()=>{const P=D.splits_plays||{};const hd=card(el("h2",{},"Plays ",badge("EXPERIMENTAL")),
  el("p",{},"One idea, tested honestly: when the public is heavy on one side (65%+ of bets across DraftKings, ScoresAndOdds/SportsBettingDime and BetMGM) but Pinnacle and Circa refuse to move toward it, play the other side. Every NFL game and every AP Top-25 college game, any day: checked once per slate, 1–2 hours before kickoff. Every pick is frozen when made and graded after the game. These are tracked picks, not official plays: the site's default stays NO BET until this record proves itself."),
  el("p",{class:"small mute"},"STRONG = Pinnacle moved against the public. HOLD = Pinnacle didn't move. Not proven profitable yet: judge it by the record below after a few weekends. Rule "+(P.rule||"")+"."));
 if(P.status!=="OK")return[hd,card(el("p",{class:"mute"},"Unavailable: "+(P.reason||P.status||"not built")))];
 const rs=P.records||[],plays=rs.filter(r=>r.status==="PICK"),now=Date.now()/1000;
 const tm=(r,side)=>{const[a,h]=r.game.split(" @ ");return side==="home"?h:side==="away"?a:side==="over"?"Over":"Under"};
 const pl=r=>{const p=r.play;return tm(r,p.side)+" "+(r.market==="spread"?ln(p.line):p.line)+" "+am(p.price)+" ("+p.book+(p.price_source&&p.price_source!=="odds feed"?", price from its splits page":"")+")"};
 const tally=xs=>{const g=xs.filter(r=>r.graded);const w=g.filter(r=>r.graded.result==="W").length,l=g.filter(r=>r.graded.result==="L").length,pu=g.length-w-l,u=g.reduce((a,r)=>a+r.graded.units,0);
  return{n:g.length,txt:g.length?w+"-"+l+(pu?"-"+pu:"")+"  ·  "+(u>=0?"+":"")+u.toFixed(2)+" units  ·  ROI "+(u/g.length*100>=0?"+":"")+(u/g.length*100).toFixed(1)+"%":"no graded plays yet"}};
 const row=(t,xs)=>{const x=tally(xs);return el("tr",{},el("td",{},t),el("td",{class:"num"},String(xs.length)),el("td",{},x.txt))};
 const rec=card(el("h3",{},"Record"),el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},""),el("th",{class:"num"},"Plays"),el("th",{},"Result"))),
  el("tbody",{},row("All plays",plays),row("NFL",plays.filter(r=>r.sport==="nfl")),row("College (Top-25)",plays.filter(r=>r.sport==="cfb")),
   row("Spreads",plays.filter(r=>r.market==="spread")),row("Totals",plays.filter(r=>r.market==="total")),
   row("STRONG",plays.filter(r=>r.play.strength==="STRONG")),row("HOLD",plays.filter(r=>r.play.strength==="HOLD"))))),
  el("p",{class:"small mute"},"Snapshots taken: "+(P.snapshots||0)+(P.latest_snapshot?" · latest "+local(P.latest_snapshot):"")+". 1 unit risked per play at the recorded price."));
 const why=r=>{const pn=r.pinnacle||{};return(r.why_public||"")+" · Pinnacle "+(pn.status||"–").toLowerCase()+(pn.age_min!=null&&pn.age_min>45?" (its latest price "+pn.age_min+" min old)":"")+" · Circa "+((r.circa||{}).status||"n/a").toLowerCase()};
 const src=r=>(r.sources||[]).map(x=>x.label+" "+Math.round(x.bets[r.public_side]||0)+"%").join(" · ");
 const up=plays.filter(r=>r.kickoff>now).sort((a,b)=>a.kickoff-b.kickoff),done=plays.filter(r=>r.kickoff<=now).sort((a,b)=>b.kickoff-a.kickoff);
 const upCard=card(el("h3",{},"Upcoming plays ("+up.length+")"),up.length?el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Kickoff"),el("th",{},"Game"),el("th",{},"Play"),el("th",{},"Strength"),el("th",{class:"hide-s"},"Public (bets %)"))),
  el("tbody",{},...up.map(r=>el("tr",{},el("td",{class:"small"},local(r.kickoff_iso)),el("td",{},r.game),el("td",{},el("b",{},pl(r))),el("td",{},r.play.strength),el("td",{class:"small hide-s"},src(r))))))):
  el("p",{class:"mute"},"No picks for upcoming games right now. Each slate is checked 1–2 hours before its first kickoff."));
 const res=r=>!r.graded?el("span",{class:"mute"},r.final?"–":"awaiting score"):el("span",{class:r.graded.result==="W"?"pos":r.graded.result==="L"?"neg":""},r.graded.result+" "+(r.graded.units>=0?"+":"")+r.graded.units.toFixed(2));
 const doneCard=card(el("h3",{},"Results ("+done.length+")"),done.length?el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Date"),el("th",{},"Game"),el("th",{},"Play"),el("th",{class:"num"},"Final (away–home)"),el("th",{},"Result"))),
  el("tbody",{},...done.map(r=>el("tr",{},el("td",{class:"small"},(r.kickoff_iso||"").slice(5,10)),el("td",{},r.game),el("td",{},pl(r)+" · "+r.play.strength),el("td",{class:"num"},r.final?r.final[1]+"–"+r.final[0]:"–"),el("td",{},res(r))))))):
  el("p",{class:"mute"},"No completed plays yet."));
 const checked=rs.slice().sort((a,b)=>b.taken_at-a.taken_at).slice(0,60);
 const chk=el("details",{},el("summary",{},"Every game checked (latest 60), and why it was or wasn't a play"),el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Game"),el("th",{},"Market"),el("th",{},"Decision"),el("th",{},"Why"))),
  el("tbody",{},...checked.map(r=>el("tr",{},el("td",{},r.game),el("td",{},r.market),el("td",{},r.status==="PICK"?el("b",{},pl(r)):r.status),el("td",{class:"small"},r.status==="PICK"?why(r):(r.reason||"")+(r.public_side?" · "+why(r):""))))))));
 return[hd,upCard,rec,doneCard,card(chk)]};
const BMV={show:{}};
R.moves=()=>{const B=D.book_moves||{};if(B.status!=="OK")return[card(el("h2",{},"Pinnacle & Circa line moves"),el("p",{class:"mute"},"Unavailable: "+(B.reason||B.status||"not built")))];
 const gs=(B.games||[]).filter(g=>g.sport===st.sport);const BK=B.books||{};
 const tm=(g,side)=>side==="home"?g.game.split(" @ ")[1]:side==="away"?g.game.split(" @ ")[0]:side==="over"?"Over":"Under";
 const nick=n=>n.split(" ").slice(-1)[0];const q=(g,m,x)=>!x?"–":m==="spreads"?nick(tm(g,"home"))+" "+ln(x.line)+" "+am(x.a)+" · "+nick(tm(g,"away"))+" "+ln(-x.line)+" "+am(x.b):"O "+x.line+" "+am(x.a)+" · U "+x.line+" "+am(x.b);
 const needEl=(g,m,c)=>{const i=(c||{}).inference||{};return i.need?el("span",{},el("b",{},tm(g,i.need)),el("span",{class:"small mute"}," ("+(i.strength==="LINE"?"number moved":"juice only")+", "+Math.abs(i.move).toFixed(1)+" pts)")):el("span",{class:"mute small"},(i.status||"–").toLowerCase())};
 const res=r=>!r?"":el("span",{class:r==="W"?"pos":r==="L"?"neg":""}," "+r);
 const recs=(B.records||[]).filter(r=>r.sport===st.sport);
 const rt=(b,m,sg)=>{const r=recs.find(x=>x.book===b&&x.market===m&&x.strength===sg);if(!r)return"–";const n=r.W+r.L;return r.W+"-"+r.L+(r.P?"-"+r.P:"")+(n?" ("+(100*r.W/n).toFixed(0)+"%)":"")};
 const recTbl=el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Book"),el("th",{},"Market"),el("th",{class:"num"},"All inferred needs"),el("th",{class:"num"},"Line moves"),el("th",{class:"num"},"Juice only"))),
  el("tbody",{},...Object.keys(BK).flatMap(b=>["spreads","totals"].map(m=>el("tr",{},el("td",{},BK[b]),el("td",{},MK[m]),el("td",{class:"num"},rt(b,m,"ALL")),el("td",{class:"num"},rt(b,m,"LINE")),el("td",{class:"num"},rt(b,m,"JUICE"))))))));
 const head=card(el("h2",{},"Pinnacle & Circa line moves ",badge("OBSERVED"),badge("EXPERIMENTAL")),
  el("p",{class:"small"},"Every price change by Pinnacle and Circa on spreads and totals, from our own collections. INFERRED NEED: the side the book made more expensive since we first saw the game (a worse number, e.g. +3 → +2.5, or more juice at the same number, e.g. −110 → −115). The book is showing that side as strong and inviting money on the cheaper side, so it wants to need it. LINE = the number moved at least half a point; JUICE = only the price moved. Moves smaller than about 8 cents of juice are shown as no clear move."),
  el("p",{class:"small mute"},B.label+". "+B.note+" Circa: our odds provider returned no Circa prices for NFL or college games when we tried it (12 Nov 2026), so its rows stay empty unless a Circa source becomes available. Rule "+B.rule+"."),
  el("h3",{},"Season record of the inferred need (every collected game, graded at the book's closing number)"),recTbl);
 const card1=g=>{const id=g.event_id;return card(el("h3",{},g.game),el("p",{class:"small mute"},local(g.kickoff)+(g.final?" · final "+g.final[1]+"–"+g.final[0]+" (away–home)":g.status==="started"?" · in progress / awaiting score":"")),
  el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Book"),el("th",{},"Market"),el("th",{},"First seen"),el("th",{},g.status==="upcoming"?"Now":"Close"),el("th",{},"Inferred need"))),
   el("tbody",{},...Object.keys(BK).flatMap(b=>["spreads","totals"].map(m=>{const c=((g.books||{})[b]||{})[m]||{};
    return el("tr",{},el("td",{},BK[b]),el("td",{},MK[m]),el("td",{class:"small"},q(g,m,c.first)),el("td",{class:"small"},q(g,m,c.last)),el("td",{},needEl(g,m,c),res(c.result)))})))))
  ,g.status==="upcoming"?el("details",BMV.show[id]?{open:""}:{},el("summary",{on:{click:e=>{e.preventDefault();BMV.show[id]=!BMV.show[id];render()}}},"Every move"),
   ...Object.keys(BK).flatMap(b=>["spreads","totals"].map(m=>{const mv=(((g.books||{})[b]||{})[m]||{}).moves||[];return mv.length?el("p",{class:"small"},el("b",{},BK[b]+" "+MK[m]+": "),mv.map(x=>local(x.at)+" "+q(g,m,x)).join(" → ")):null}))):null)};
 const up=gs.filter(g=>g.status==="upcoming"),past=gs.filter(g=>g.status!=="upcoming").reverse();
 return[head,el("h2",{class:"sec"},"Upcoming ("+up.length+")"),...(up.length?up.map(card1):[card(el("p",{class:"mute"},"No upcoming games with Pinnacle or Circa prices."))]),
  el("h2",{class:"sec"},"Completed, last 3 weeks ("+past.length+")"),...past.map(card1)]};
function agreeCard(A){if(!A)return card(el("h2",{},"Market NEED × synthetic sharp agreement"),el("p",{class:"mute"},"Research records unavailable in this build."));
 const seg=(k,opts)=>el("span",{class:"seg"},...opts.map(([v,t])=>el("button",{class:AG[k]===v?"on":"",on:{click:()=>{AG[k]=v;render()}}},t)));
 const rec=t=>!t||!t.n?"–":t.W+"-"+t.L+(t.P?"-"+t.P:"");const u=t=>!t||!t.n?"–":(t.units>0?"+":"")+t.units.toFixed(2)+"u";
 const roi=t=>!t||!t.n||t.roi==null?"–":(t.roi>0?"+":"")+(100*t.roi).toFixed(1)+"%";const wp=t=>!t||t.win_pct==null?"–":(100*t.win_pct).toFixed(1)+"%";
 const clv=t=>!t||t.clv==null?"–":(t.clv>0?"+":"")+(100*t.clv).toFixed(2)+"%";
 const sel=x=>x.rule===AG.rule&&x.cut===AG.cut&&x.sport===st.sport&&x.market===AG.market;
 const recs=A.records.filter(r=>sel(r)&&r.source===AG.src).sort((a,b)=>Object.keys(AGCAT).indexOf(a.cat)-Object.keys(AGCAT).indexOf(b.cat));
 const tbl=el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Category"),el("th",{class:"num"},"Games"),
   el("th",{class:"num"},"NEED side W-L-P"),el("th",{class:"num"},"Win %"),el("th",{class:"num"},"Units"),el("th",{class:"num"},"ROI"),el("th",{class:"num hide-s"},"CLV"),
   el("th",{class:"num"},"Sharp side W-L-P"),el("th",{class:"num"},"Units"),el("th",{class:"num hide-s"},"ROI"))),
  el("tbody",{},...recs.map(r=>el("tr",{class:r.cat==="ALL_NEED"?"tot":""},el("td",{},el("a",{href:"javascript:void 0",on:{click:()=>{AG.cat=AG.cat===r.cat?"":r.cat;AG.show=true;render()}}},AGCAT[r.cat]||r.cat)),
   el("td",{class:"num"},r.games),el("td",{class:"num"},rec(r.need_side)),el("td",{class:"num"},wp(r.need_side)),el("td",{class:"num"},u(r.need_side)),el("td",{class:"num"},roi(r.need_side)),el("td",{class:"num hide-s"},clv(r.need_side)),
   el("td",{class:"num"},rec(r.sharp_side)),el("td",{class:"num"},u(r.sharp_side)),el("td",{class:"num hide-s"},roi(r.sharp_side)))))));
 const gs=A.games.filter(g=>sel(g)&&(!AG.cat||(AG.cat==="ALL_NEED"?g.need:(g[AG.src]&&g[AG.src].cat===AG.cat)))).sort((a,b)=>(b.kickoff||"").localeCompare(a.kickoff||""));
 const res=b=>!b?el("span",{class:"mute"},"–"):el("span",{class:b.res==="W"?"pos":b.res==="L"?"neg":""},b.res+" "+(b.units>0?"+":"")+b.units.toFixed(2));
 const rows=gs.map(g=>{const x=g[AG.src];return el("tr",{},el("td",{class:"small"},(g.kickoff||"").slice(5,10)),el("td",{},g.game),
   el("td",{class:"num small"},g.final?g.final[1]+"–"+g.final[0]:"–"),el("td",{},g.need?el("b",{},g.need.toUpperCase()+(g.strength?" ("+g.strength+")":"")):el("span",{class:"mute"},"PASS")),
   el("td",{class:"small"},!x?el("span",{class:"mute"},"no ledger"):x.dir?x.dir.toUpperCase()+" ("+x.str+", "+x.tickets+" tix / "+x.bettors+" bettors)":(x.status||"").replace(/_/g," ").toLowerCase()),
   el("td",{class:"small"},x?(AGCAT[x.cat]||x.cat):"–"),el("td",{class:"num"},res(g.need_bet)),el("td",{class:"num"},res(x&&x.sharp_bet)))});
 const list=el("details",AG.show?{open:""}:{},el("summary",{on:{click:e=>{e.preventDefault();AG.show=!AG.show;render()}}},"Every game ("+gs.length+(AG.cat?", "+AGCAT[AG.cat]:"")+")"),
  el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Date"),el("th",{},"Game"),el("th",{class:"num"},"Final (away–home)"),el("th",{},"NEED"),el("th",{},"Synthetic sharps"),el("th",{},"Category"),el("th",{class:"num"},"NEED bet"),el("th",{class:"num"},"Sharp bet"))),el("tbody",{},rows))));
 return card(el("h2",{},"Market NEED × synthetic sharp agreement: 2026 records ",badge("EXPERIMENTAL"),badge("MODELED")),
  el("p",{class:"small"},A.status+". "+A.label+". Games "+(A.games_from||"").slice(0,10)+" to "+(A.games_to||"").slice(0,10)+" ("+(st.sport==="nfl"?"NFL":"college football")+"). Graded at the "+A.grading+". The NEED side is a replay of the original engine on collected quotes, not a frozen pre-game record; sample sizes per category are small. Not official plays; default NO BET."),
  el("div",{class:"filters"},seg("market",[["spread","Spreads"],["total","Totals"]])," ",seg("cut",[["T-1h","1 h before kickoff"],["T-6h","6 h before"]])," ",
   seg("src",[["genuine","Sharps as published"],["recon","Sharps reconstructed"]])," ",seg("rule",[["v3","Original freshness"],["v4","V4 freshness"]])),
  el("p",{class:"small mute"},"Sharps as published = synthetic-sharp positions from ledger files saved before the cutoff (only since the book went live). Reconstructed = the same engine replayed over the whole season afterwards. Click a category to list its games."),
  tbl,list)}
function shp(t,x,sides){const s=x.sharp||{},a=x.agreement||{};const pc=v=>v==null?"–":Math.round(100*v)+"%";
 return el("div",{},el("h3",{},t+": NEED × synthetic sharps ",badge("MODELED")),
  el("p",{class:"small"},el("b",{},(a.category||"–").replace(/_/g," "))," · "+(a.why||"")),
  s.status&&s.status!=="OK"&&!s.tickets?el("p",{class:"small mute"},s.status+(s.reason?" ("+s.reason+")":"")):
  el("p",{class:"small"},"Synthetic sharp tickets "+(s.tickets||0)+" from "+(s.bettors||0)+" bettors · tickets "+sides.map(k=>k+" "+pc((s.ticket_pct||{})[k])).join(" / ")+
   " · simulated handle "+sides.map(k=>k+" "+pc((s.handle_pct||{})[k])).join(" / ")+" · largest wager "+pc(s.largest_wager_share)+" of sharp handle · lean "+(s.direction||"none")+(s.strength?" ("+s.strength+")":"")),
  el("p",{class:"small mute"},(s.label||"SIMULATED")+(s.book_simulated_through?" · book simulated through "+s.book_simulated_through:"")+(s.last_at?" · last sharp wager "+s.last_at:"")))}
function render(){const h=location.hash.slice(1);let sec=h||"plays",gid=null;if(h.startsWith("game/")){gid=h.slice(5);sec="game"}
 if(sec!=="game"&&!R[sec])sec="plays";if(sec!=="game")st.prev=sec;st.sec=sec;
 for(const b of document.querySelectorAll("#sport button"))b.classList.toggle("on",b.dataset.s===st.sport);
 const nav=document.getElementById("nav");const showMore=st.more||(sec!=="plays"&&sec!=="circa"&&sec!=="game");
 nav.replaceChildren(el("a",{href:"#plays",class:sec==="plays"?"on":""},"Plays"),el("a",{href:"#circa",class:sec==="circa"?"on":""},"Circa 99%"),
  ...(showMore?SECTIONS.concat(SECTIONS_MI).map(([k,t])=>el("a",{href:"#"+k,class:k===sec?"on":""},t)):[]),
  el("a",{href:"javascript:void 0",class:"small",on:{click:()=>{st.more=!showMore;try{localStorage.setItem("v4more",st.more?"1":"")}catch(e){}if(!st.more&&sec!=="plays"&&sec!=="circa")location.hash="plays";else render()}}},showMore?"Hide research tabs":"Research tabs ▸"));
 const s=S();const f=freshness(s.observed_at);document.getElementById("fresh").replaceChildren(badge(f),el("span",{class:"mute"},s.observed_at?fmtAge(ageMin(s.observed_at))+" old":"no data"));
 const main=document.getElementById("main");const y=window.scrollY;main.replaceChildren(...(sec==="game"?R.game(gid):R[sec]()).filter(Boolean));
 if(document.activeElement&&document.activeElement.type==="search"){}else window.scrollTo(0,y)}
document.getElementById("sport").addEventListener("click",e=>{const s=e.target.dataset&&e.target.dataset.s;if(!s)return;st.sport=s;
 try{localStorage.setItem("v4sport",s)}catch(e){}if(location.hash.startsWith("#game/"))location.hash=st.prev||"overview";else render()});
window.addEventListener("hashchange",()=>{render();window.scrollTo(0,0)});setInterval(()=>{if(document.activeElement&&document.activeElement.tagName==="INPUT")return;render()},60000);render();
</script></body></html>"""


def render_html(snapshot):
    data = json.dumps(snapshot, separators=(",", ":"), default=str)
    data = data.replace("<", "\\u003c")      # no markup at all inside the data block (JSON.parse restores it)
    return PAGE.replace("__DATA__", data)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", help="directory of quotes_*.jsonl.gz shards (searched recursively)")
    ap.add_argument("--live-db", help="an existing market store instead of --shards")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--rankings-dir", default=None)
    ap.add_argument("--out", default="site")
    ap.add_argument("--bettors", type=int, default=10_000)
    ap.add_argument("--now", default=None, help="ISO time to build as of (default: current time)")
    ap.add_argument("--book-dir", default=None, help="persisted synthetic-book ledger files (release v4-book)")
    ap.add_argument("--book-out", default=None, help="where to write ledger files created by this build")
    ap.add_argument("--need-dir", default=None, help="saved Need-tracker snapshots and decisions (v4-need*)")
    ap.add_argument("--results-dir", default=None, help="saved final scores (release v4-results)")
    ap.add_argument("--need-out", default=None, help="where to write Need-tracker files created by this build")
    ap.add_argument("--settle-dir", default=None, help="saved wager settlements (release v4-settlement)")
    ap.add_argument("--sensitivity-cache", default=None, help="previous build's variant splits (v4-site, optional)")
    ap.add_argument("--line-shop-cache", default=None,
                    help="previous build's line-shopping scan cache (v4-site, optional; performance only)")
    ap.add_argument("--circa-dir", default=None, help="Circa 99%% engine records (release v4-circa)")
    ap.add_argument("--circa-out", default=None, help="where to write Circa finals saved by this build")
    ap.add_argument("--splits-dir", default=None, help="public-splits snapshots and sealed plays (release v4-splits)")
    ap.add_argument("--splits-out", default=None, help="where to write play records sealed by this build")
    ap.add_argument("--mi-out", default=None, help="market-intelligence records created by this build (if enabled)")
    ap.add_argument("--mi-existing", default=None, help="names of saved market-intelligence records (one per line)")
    a = ap.parse_args(argv)
    now = _epoch(a.now) if a.now else time.time()
    if a.live_db:
        conn, n_shards = sqlite3.connect(f"file:{a.live_db}?mode=ro", uri=True), None
    else:
        conn, _counts, n_shards = rebuild_store(a.shards)
    ledger_rows = [json.loads(x) for x in open(a.ledger) if x.strip()] if a.ledger and os.path.exists(a.ledger) else []
    os.makedirs(a.out, exist_ok=True)
    bpath = os.path.join(a.out, "synthetic_book.db")
    if os.path.exists(bpath):
        os.remove(bpath)
    bconn = sqlite3.connect(bpath)
    ensure_schema(bconn)
    cache = None
    if a.sensitivity_cache and os.path.exists(a.sensitivity_cache):
        try:
            cache = json.load(open(a.sensitivity_cache))
        except ValueError:
            cache = None           # unreadable cache: simply recompute
    from sportsbook_v4 import need_tracker as _NT
    try:      # settled results of earlier weeks set V5 week-open bankrolls (verified again by settlement_step)
        prior_settlements = _NT.load(a.settle_dir, "settle_*.json.gz") if a.settle_dir else []
    except _NT.TrackerIntegrityError:
        prior_settlements = []
    from analysis_v4.line_shop_cache import LineShopCache
    ls_cache = LineShopCache(a.line_shop_cache)       # missing/corrupt/old -> full calculation (same numbers)
    snap = build_snapshot(conn, ledger_rows, load_rankings(a.rankings_dir), now, bconn, a.bettors, book_in=a.book_dir,
                          book_out=a.book_out, sensitivity_cache=cache, settlements=prior_settlements,
                          line_shop_cache=ls_cache,
                          commit=(os.environ.get("GITHUB_SHA") or "")[:7] or None)
    snap["shards"] = n_shards
    tracker_step(snap, load_rankings(a.rankings_dir), now, a.need_dir, a.results_dir, a.need_out, conn)
    settlement_step(snap, bconn, conn, load_rankings(a.rankings_dir), now, a.results_dir, a.settle_dir, a.need_out)
    try:                                   # experimental and separate: can never stop the build
        official_step(snap, load_rankings(a.rankings_dir), now, a.need_dir, a.results_dir, a.need_out)
    except Exception as e:                 # noqa: BLE001
        for sp in SPORTS:
            (snap["sports"][sp].get("book") or {}).pop("official_snapshots", None)
        snap["official_needs"] = {"status": "ERROR", "reason": f"{type(e).__name__}: {e}"[:300]}
    book_moves_step(snap, conn, load_rankings(a.rankings_dir), a.results_dir, now)
    splits_step(snap, conn, load_rankings(a.rankings_dir), a.results_dir, now, a.splits_dir, a.splits_out)
    circa_step(snap, a.circa_dir, a.circa_out)
    market_intel_step(snap, conn, load_rankings(a.rankings_dir), a.results_dir, now, a.mi_out, a.mi_existing, bconn)
    with open(os.path.join(a.out, "sensitivity_cache.json"), "w") as fh:
        json.dump(snap.pop("_sensitivity_cache", None), fh, separators=(",", ":"))
    ls_cache.save(os.path.join(a.out, "line_shop_cache.json.gz"))
    print(f"line-shop cache: {ls_cache.load_status}; " + "; ".join(
        f"{sp}: {st['status']}, reused {st['reused']}, computed {st['computed']}, invalidated {st['invalidated']}, "
        f"spot-checked {st['spot_checked']}" for sp, st in ls_cache.stats.items()))
    try:                                   # display only; never stops the build
        feed_kickoffs(snap, conn, now)
    except Exception as e:                 # noqa: BLE001
        print(f"::warning::feed kickoff correction skipped: {type(e).__name__}: {e}")
    snap = _clean(snap)
    bconn.close()
    with open(os.path.join(a.out, "site_snapshot.json"), "w") as fh:
        json.dump(snap, fh, separators=(",", ":"))
    with open(os.path.join(a.out, "index.html"), "w") as fh:
        fh.write(render_html(snap))
    s = snap["sports"]
    print(f"site built: NFL {len(s['nfl']['games'])} games, CFB {len(s['cfb']['games'])} games, "
          f"scanner {len(s['nfl']['scanner']) + len(s['cfb']['scanner'])} rows, "
          f"book NFL {s['nfl']['book']['status']} / CFB {s['cfb']['book']['status']}")


if __name__ == "__main__":
    main()
