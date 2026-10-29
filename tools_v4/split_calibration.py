"""
tools_v4/split_calibration.py -- compare MODELED splits (active m4 or an experimental candidate) with OBSERVED
third-party splits (data_v4/observed_splits). Offline; nothing is persisted; missing observations stay missing.

For every observation (sport, game, book, market, selection, time) the modeled split is taken from the model's
wagers accepted at or before the observation time on the same game and market (all simulated customers, as
a book's published split covers all its customers). Then:
  * ticket % and handle % error: MAE, RMSE, mean bias (modeled - observed)
  * directional agreement: does the model's majority side match the observed majority side
  * calibration by observed public pressure: observed majority share in bins 50-60 / 60-70 / 70-80 / 80+,
    mean observed vs mean modeled
  * breakdowns by sport, market, sportsbook and game popularity tercile (when a popularity index exists)
Training vs held-out: observations are split CHRONOLOGICALLY by kickoff date (--holdout-from). Choosing
between models or parameter values may use the training period only; the held-out period is reported once.
"""

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sportsbook_v4.util import to_epoch  # noqa: E402

BINS = ((50, 60), (60, 70), (70, 80), (80, 101))
MIN_HOLDOUT_FOR_CLAIMS = 100


def modeled_split(wagers, market, selection, at_epoch):
    """(ticket_pct, handle_pct) of `selection` among wagers on one game and market up to at_epoch, or None."""
    ws = [w for w in wagers if w["market"] == market and to_epoch(w["accepted_at"]) <= at_epoch]
    if not ws:
        return None
    n, h = len(ws), math.fsum(w["stake"] for w in ws)
    sel = [w for w in ws if w["side"] == selection]
    return 100.0 * len(sel) / n, (100.0 * math.fsum(w["stake"] for w in sel) / h if h else None)


def pair(rows):
    """{(game key, book, market, observed_at): {selection: row}} for directional comparisons."""
    out = {}
    for r in rows:
        out.setdefault((r["game_key"], r["sportsbook"], r["market"], r["observed_at"]), {})[r["selection"]] = r
    return out


def _err(rows, f):
    e = [r["modeled_" + f] - r[f] for r in rows if r.get(f) is not None and r.get("modeled_" + f) is not None]
    if not e:
        return {"n": 0}
    return {"n": len(e), "mae": round(sum(abs(x) for x in e) / len(e), 2),
            "rmse": round(math.sqrt(sum(x * x for x in e) / len(e)), 2), "bias": round(sum(e) / len(e), 2)}


def summarize(rows):
    out = {"observations": len(rows), "ticket_pct": _err(rows, "ticket_pct"), "handle_pct": _err(rows, "handle_pct")}
    agree = {"ticket_pct": [0, 0], "handle_pct": [0, 0]}
    calib = {f"{lo}-{min(hi, 100)}": {"n": 0, "obs": 0.0, "mod": 0.0} for lo, hi in BINS}
    for sides in pair(rows).values():
        if len(sides) != 2:
            continue
        a, b = sides.values()
        for f in agree:
            if None in (a.get(f), b.get(f), a.get("modeled_" + f), b.get("modeled_" + f)) or a[f] == b[f]:
                continue
            obs_major = a if a[f] > b[f] else b
            other = b if obs_major is a else a
            agree[f][1] += 1
            agree[f][0] += obs_major["modeled_" + f] > other["modeled_" + f]
            if f == "handle_pct":
                for lo, hi in BINS:
                    if lo <= obs_major[f] < hi:
                        c = calib[f"{lo}-{min(hi, 100)}"]
                        c["n"] += 1
                        c["obs"] += obs_major[f]
                        c["mod"] += obs_major["modeled_" + f]
    out["directional_agreement"] = {f: {"n": n, "agree": k, "rate": round(k / n, 3) if n else None}
                                    for f, (k, n) in agree.items()}
    out["handle_calibration_by_observed_pressure"] = {
        k: ({"n": v["n"], "mean_observed": round(v["obs"] / v["n"], 1), "mean_modeled": round(v["mod"] / v["n"], 1)}
            if v["n"] else {"n": 0}) for k, v in calib.items()}
    return out


def evaluate(rows, holdout_from, popularity=None):
    """rows: observations with modeled_ticket_pct / modeled_handle_pct filled (None when the model had no wagers)."""
    for r in rows:
        r["period"] = "holdout" if r["kickoff_et_date"] >= holdout_from else "train"
        if popularity:
            r["popularity"] = max(popularity.get(r.get("home_id"), 1.0), popularity.get(r.get("away_id"), 1.0))
    res = {"holdout_from": holdout_from, "label": "MODELED vs OBSERVED_THIRD_PARTY",
           "unmatched_or_no_model_wagers": sum(1 for r in rows if r.get("modeled_ticket_pct") is None)}
    usable = [r for r in rows if r.get("modeled_ticket_pct") is not None]
    for period in ("train", "holdout"):
        sub = [r for r in usable if r["period"] == period]
        res[period] = summarize(sub)
        for key in ("sport", "market", "sportsbook"):
            res[period]["by_" + key] = {v: summarize([r for r in sub if r[key] == v]) for v in sorted({r[key] for r in sub})}
        if popularity and sub:
            vals = sorted(r["popularity"] for r in sub)
            t1, t2 = vals[len(vals) // 3], vals[2 * len(vals) // 3]
            res[period]["by_popularity_tercile"] = {
                "low": summarize([r for r in sub if r["popularity"] <= t1]),
                "mid": summarize([r for r in sub if t1 < r["popularity"] <= t2]),
                "high": summarize([r for r in sub if r["popularity"] > t2])}
    n_hold = res["holdout"]["observations"]
    res["verdict"] = ("NO OBSERVED DATA" if not rows else
                      f"INSUFFICIENT HOLDOUT ({n_hold} < {MIN_HOLDOUT_FOR_CLAIMS} observations)"
                      if n_hold < MIN_HOLDOUT_FOR_CLAIMS else "EVALUATED")
    return res


def match(observations, games, cfb_matcher=None):
    """Attach event ids: games = [{sport, event_id, home, away, kickoff}] with Odds API names."""
    import datetime
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")

    def same(sport, a, b):
        if sport == "cfb" and cfb_matcher is not None:
            return cfb_matcher.school_for(a) is not None and cfb_matcher.school_for(a) == cfb_matcher.school_for(b)
        return a.strip().lower() == b.strip().lower()
    out = []
    for o in observations:
        cands = [g for g in games if g["sport"] == o["sport"] and same(o["sport"], o["home"], g["home"])
                 and same(o["sport"], o["away"], g["away"])
                 and datetime.datetime.fromtimestamp(to_epoch(g["kickoff"]), et).date().isoformat() == o["kickoff_et_date"]]
        r = dict(o, game_key=None, event_id=None)
        if len(cands) == 1:
            r.update(event_id=cands[0]["event_id"], game_key=(o["sport"], cands[0]["event_id"]))
        out.append(r)
    return out


def attach(rows, wagers_by_game):
    """Fill modeled_ticket_pct / modeled_handle_pct from {(sport, event_id): wagers}; None when unavailable."""
    for r in rows:
        m = modeled_split(wagers_by_game.get(r["game_key"], []), r["market"], r["selection"], to_epoch(r["observed_at"])) \
            if r["game_key"] else None
        r["modeled_ticket_pct"], r["modeled_handle_pct"] = (m if m else (None, None))
    return rows


def run(observations, book_conn, live_conn, holdout_from, candidate=None, cfb_matcher=None):
    """Evaluate the active model (candidate=None, read from the ledger) or a params_calib candidate (replayed)."""
    from sportsbook_v4 import ENGINE_VERSION, live_book as LB, params as P, params_calib
    from sportsbook_v4.engine import load_run
    from sportsbook_v4.ledger_read import fetch_wagers
    games, by_game, pops = [], {}, {}
    for rid, sport in book_conn.execute("SELECT run_id, sport FROM v4_run WHERE engine_version=? AND params_hash=?",
                                        (ENGINE_VERSION, P.params_hash())).fetchall():
        slate = load_run(book_conn, rid)["slate"]
        for g in slate.games:
            home, away = LB._names(live_conn, g.canonical_game_id)
            if home:
                games.append({"sport": sport, "event_id": g.canonical_game_id, "home": home, "away": away,
                              "kickoff": g.commence_time})
        if candidate is None:
            ws = fetch_wagers(book_conn, rid, market=None)
        else:
            teams = sorted({t for g in slate.games for t in (g.home_team_id, g.away_team_id)})
            kw = params_calib.CANDIDATES[candidate](sport, teams)
            pops.update(kw.get("team_popularity") or {})
            wm = LB._watermark_iso(book_conn, rid)
            mem, mrid = LB.replay(book_conn, live_conn, rid, wm, wm, variant=(candidate, lambda: kw))
            ws = fetch_wagers(mem, mrid, market=None)
        for w in ws:
            by_game.setdefault((sport, w["canonical_game_id"]), []).append(w)
    rows = attach(match(observations, games, cfb_matcher), by_game)
    res = evaluate(rows, holdout_from, pops or None)
    res["model"] = candidate or "active m4 (ledger)"
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--observed-dir", required=True)
    ap.add_argument("--holdout-from", required=True, help="kickoff ET date (YYYY-MM-DD) where the held-out period starts")
    ap.add_argument("--shards", default=None)
    ap.add_argument("--book-dir", default=None)
    ap.add_argument("--candidate", default=None)
    a = ap.parse_args(argv)
    from data_v4 import observed_splits
    obs = observed_splits.load(a.observed_dir)
    if not obs or not (a.shards and a.book_dir):
        print(json.dumps(evaluate([], a.holdout_from) if not obs else {"error": "--shards and --book-dir are required"}))
        return
    import sqlite3
    from sportsbook_v4 import persist
    from sportsbook_v4.schema import ensure_schema
    from web_v4.live_site import rebuild_store
    live, _c, _n = rebuild_store(a.shards)
    book = sqlite3.connect(":memory:")
    ensure_schema(book)
    persist.load_dir(book, a.book_dir)
    print(json.dumps(run(obs, book, live, a.holdout_from, a.candidate), indent=1))


if __name__ == "__main__":
    main()
