"""
intel_v7/site.py -- V7 block for the existing site build (web_v4/live_site.py). Runs ONLY when V7_INTEL=on.

No API calls: it reads the market store the build already has. Per sport, for the current betting week:
  * upcoming games: the latest card (data through the build's complete-through time);
  * started games: the FINAL PREGAME card (latest fetch at or before kickoff - 30 min), written ONCE as
    v7intel_<sport>_<event>_<market>.json.gz into the week's v4-need-YYYY-Www folder (never matches need_*);
  * validation: every saved frozen record written before a final could exist, graded at its frozen best price.
Idempotent: cards are recomputed from immutable quotes; frozen records are never rewritten.
"""
import glob
import gzip
import json
import os
import time

from intel_v7 import LABEL, VERSION
from intel_v7.classify import card
from intel_v7.config import CONFIG, CONFIG_HASH
from intel_v7.convergence import Convergence
from intel_v7.evaluate import close_state, outcome
from intel_v7.history import History, games, view
from sportsbook_v4 import live_book as LB
from sportsbook_v4.util import from_epoch, to_epoch

PREFIX = "v7intel_"


def record_name(sport, event_id, market):
    return f"{PREFIX}{sport}_{event_id}_{market}.json.gz"


def release_tag(kickoff_iso):
    return "v4-need-" + time.strftime("%G-W%V", time.gmtime(to_epoch(kickoff_iso)))


def existing(d):
    out = {}
    for p in glob.glob(os.path.join(d or "", "**", PREFIX + "*.json.gz"), recursive=True):
        try:
            out[os.path.basename(p)] = json.load(gzip.open(p, "rt"))
        except (OSError, ValueError):
            continue
    return out


def write_once(rec_card, need_dir, out_dir, now, have):
    name = record_name(rec_card["sport"], rec_card["event_id"], rec_card["market"])
    if name in have or not out_dir:
        return None
    kick = to_epoch(rec_card["kickoff"])
    if now < kick:
        return None
    rec = {"format": "intel_v7_frozen_v1", "version": VERSION, "config_hash": CONFIG_HASH,
           "written_at": from_epoch(now), "written_before_final": now - kick <= CONFIG["write_before_final_s"],
           "card": rec_card}
    folder = os.path.join(out_dir, release_tag(rec_card["kickoff"]))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    if os.path.exists(path):
        return None
    with gzip.open(path, "wt") as f:
        json.dump(rec, f, sort_keys=True, default=str)
    have[name] = rec
    return name


def convergence_from_store(conn, sport, before):
    conv = Convergence()
    for e, k, h, a in games(conn, sport, end=before):
        for m in ("spread", "total"):
            hist = History(conn, sport, e, m)
            if hist.snaps:
                conv.add_history(hist)
    return conv


def grade(conn, rec, finals):
    """Prospective grade of one frozen record (SIGNAL and every lean): at the frozen best executable price."""
    c = rec["card"]
    bp = c.get("best_observed_executable_price")
    if not c.get("side") or not bp:
        return None
    hist = History(conn, c["sport"], c["event_id"], c["market"])
    close = close_state(hist) if hist.kickoff else None
    if close is None:
        return {"signal": c["signal_or_pass"] == "SIGNAL", "status": "NO_CLOSE"}
    _, books = hist.snap_at(to_epoch(c["as_of"]))
    o = outcome(hist, c["side"], bp["point"], bp["price"], view(books), close, finals.get((c["sport"], c["event_id"])))
    return {"signal": c["signal_or_pass"] == "SIGNAL", **o}


def validation(conn, recs, finals):
    agg = {}
    late = 0
    for rec in recs:
        if not rec.get("written_before_final"):
            late += 1
            continue
        g = grade(conn, rec, finals)
        key = "SIGNAL" if rec["card"]["signal_or_pass"] == "SIGNAL" else "PASS"
        a = agg.setdefault(key, {"records": 0, "directional": 0, "w": 0, "l": 0, "p": 0, "units": 0.0,
                                 "clv_consensus": [], "pending": 0})
        a["records"] += 1
        if not g or g.get("status") == "NO_CLOSE":
            continue
        a["directional"] += 1
        a["clv_consensus"].append(g["clv_consensus_close"])
        if g["result"] is None:
            a["pending"] += 1
        else:
            a[{"win": "w", "loss": "l", "push": "p"}[g["result"]]] += 1
            a["units"] += g["units"] or 0.0
    for a in agg.values():
        cl = a.pop("clv_consensus")
        a["record"] = f"{a['w']}-{a['l']}-{a['p']}"
        a["roi"] = round(a["units"] / (a["w"] + a["l"]), 4) if a["w"] + a["l"] else None
        a["mean_clv_consensus_close"] = round(sum(cl) / len(cl), 4) if cl else None
    return {"basis": "PROSPECTIVE frozen V7 records written before any final; graded at the frozen best executable price",
            "excluded_late_written": late, "by_type": agg}


def build_block(conn, rankings_snapshots, now, results_dir=None, need_dir=None, need_out=None):
    block = {"status": "OK", "label": LABEL, "version": VERSION, "config_hash": CONFIG_HASH, "built_at": from_epoch(now),
             "actual_book_liability": "UNKNOWN", "sports": {}}
    have = existing(need_dir)
    have.update(existing(need_out))
    written = []
    for sport in ("nfl", "cfb"):
        L = LB.last_observation(conn, sport, now)
        if L is None:
            block["sports"][sport] = {"status": "NO_DATA", "cards": []}
            continue
        D = LB.complete_through(L, now)
        ws, we = LB.week_window(D)
        gl, cov = LB.select_week(conn, sport, ws, we, D, rankings_snapshots if sport == "cfb" else None)
        conv = convergence_from_store(conn, sport, D)
        cards = []
        for g in gl:
            for m in ("spread", "total"):
                hist = History(conn, sport, g["event_id"], m, until=D)
                if not hist.snaps or hist.kickoff is None:
                    continue
                if hist.kickoff > D:
                    cards.append(card(hist, D, conv))
                else:
                    t = hist.kickoff - CONFIG["final_snapshot_before_kick_s"]
                    n = write_once(card(hist, t, conv), need_dir, need_out, now, have)
                    if n:
                        written.append(n)
        order = {"SIGNAL": 0, "PASS": 1}
        cards.sort(key=lambda c: (order[c["signal_or_pass"]], c["kickoff"], c["game"], c["market"]))
        block["sports"][sport] = {"status": "OK" if gl else "NO_GAMES", "data_through": from_epoch(D), "coverage": cov,
                                  "cards": cards, "signals": sum(c["signal_or_pass"] == "SIGNAL" for c in cards)}
    block["frozen_written_this_build"] = len(written)
    recs = list(have.values())
    finals = {}
    if results_dir and recs:
        from data_v4 import rankings
        from data_v4 import results as R
        from need_v6 import results_match as RM
        snaps = [s for s in rankings_snapshots or [] if s.get("teams")]
        mt = rankings.TeamMatcher(max(snaps, key=lambda s: s["fetched_at"])["teams"]) if snaps else None
        gm = [{"sport": r["card"]["sport"], "event_id": r["card"]["event_id"], "home": r["card"]["home"],
               "away": r["card"]["away"], "kickoff": r["card"]["kickoff"]} for r in recs]
        finals = {k: (v["home_score"], v["away_score"]) for k, v in RM.match(gm, R.load(results_dir), mt).items()}
    block["validation"] = validation(conn, recs, finals)
    return block


def step(snap, conn, rankings_snapshots, now, results_dir, need_dir, need_out):
    t0 = time.time()
    snap["intel_v7"] = build_block(conn, rankings_snapshots, now, results_dir, need_dir, need_out)
    snap["intel_v7"]["seconds"] = round(time.time() - t0, 1)
