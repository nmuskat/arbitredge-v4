"""
web_v4/snapshot.py -- assemble everything the website shows into ONE JSON
file (data/site/snapshot.json). The web app only reads that file, so the
site can never write to any ledger or reach any provider.

  python -m web_v4.snapshot --as-of 2026-10-26T21:00:00Z

Provenance labels (shown as badges on every panel):
  OBSERVED      real external data (nflverse results/listed lines)
  MODEL         Engine C projections
  SYNTHETIC     Engine A simulated customers and their wagers
  EXPERIMENTAL  uncalibrated heuristics (Engine B score)
  DEMO          simulated market quotes (no live odds feed in this build)
  VALIDATED     passed the evidence gate (nothing yet)
"""

import argparse
import json
import os
import sqlite3
import subprocess
import time

import pandas as pd

from edge_v4 import ev, forward, ledger
from market_v4 import asof, quality, store
from sharp_v4 import signals
from sims_v4 import distributions as D
from sims_v4 import model as M
from sportsbook_v4 import settlement
from sportsbook_v4.engine import GameSpec, SlateSpec, advance, create_run
from sportsbook_v4.facts import NoFacts
from sportsbook_v4.ledger_read import fetch_wagers
from sportsbook_v4.market import MarketV4Source
from sportsbook_v4.population import ensure_population
from sportsbook_v4.schema import ensure_schema
from web_v4 import demo_market

TEAMS = {"ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
         "CAR": "Carolina Panthers", "CHI": "Chicago Bears", "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
         "DAL": "Dallas Cowboys", "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
         "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars", "KC": "Kansas City Chiefs",
         "LA": "Los Angeles Rams", "LAC": "Los Angeles Chargers", "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins",
         "MIN": "Minnesota Vikings", "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
         "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers", "SEA": "Seattle Seahawks",
         "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers", "TEN": "Tennessee Titans", "WAS": "Washington Commanders"}


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _clean(o):
    """JSON-safe: tuples->lists, numpy->float, NaN->None."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item"):
        o = o.item()
    if isinstance(o, float) and o != o:
        return None
    return o


def upcoming_games(features, as_of):
    t = pd.Timestamp(as_of, tz="UTC") if not isinstance(as_of, pd.Timestamp) else as_of
    g = features[(~features.played) & features.spread_line.notna() & (features.kickoff_utc > t)]
    return g.sort_values(["kickoff_utc", "game_id"])


def sims_block(pred, r):
    pmf = pred["margin_pmf"]
    tpmf = pred["total_pmf"]
    hl = -float(r.spread_line)
    c, pu, f = D.margin_probs(pmf, hl)
    o, tpu, u = D.total_probs(tpmf, float(r.total_line))
    top = sorted(pmf.items(), key=lambda kv: -kv[1])[:8]
    return {"mu_margin": pred["mu_margin"], "se_margin": pred["se_margin"], "sigma_margin": pred["sigma_margin"],
            "mu_total": pred["mu_total"], "fair_home_spread": round(pred["fair_home_spread"] * 2) / 2,
            "fair_home_spread_exact": pred["fair_home_spread"], "fair_total": pred["fair_total"],
            "p_home_win": pred["p_home_win"], "p_tie": pred["p_tie"],
            "listed_home_line": hl, "p_home_cover_listed": c, "p_push_listed": pu, "p_away_cover_listed": f,
            "listed_total": float(r.total_line), "p_over_listed": o, "p_total_push": tpu, "p_under_listed": u,
            "margin_hist": [[m, round(p, 5)] for m, p in sorted(pmf.items()) if -35 <= m <= 35],
            "most_likely_margins": [[m, round(p, 4)] for m, p in top]}


def load_rankings(rankings_dir):
    """CFBD AP-poll snapshots (rankings_*.json from release cfb-rankings); [] when none."""
    if not rankings_dir or not os.path.isdir(rankings_dir):
        return []
    return [json.load(open(os.path.join(rankings_dir, f))) for f in sorted(os.listdir(rankings_dir))
            if f.startswith("rankings_") and f.endswith(".json")]


def live_block(live_db, ledger_path, T, rankings_dir=None, book_path=None, bettors=10_000):
    """Views over GENUINE collected odds (OBSERVED), plus the synthetic book run on them (MODELED).
    None when no live store is given."""
    from sportsbook_v4 import live_book
    from web_v4 import live
    conn = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
    ledger_rows = []
    if ledger_path and os.path.exists(ledger_path):
        ledger_rows = [json.loads(x) for x in open(ledger_path) if x.strip()]
    out = {"boards": {}, "scanner": [], "history": {}, "sharp": []}
    for sport in ("americanfootball_nfl", "americanfootball_ncaaf"):
        out["boards"][sport] = live.board(conn, sport, T)
        rows, hist = live.scanner_rows(conn, sport, T)
        out["scanner"] += rows
        out["history"][sport] = hist
        out["sharp"] += live.sharp_rows(conn, sport, T)
    order = {"BET": 0, "WATCH": 1, "NO BET": 2}
    out["scanner"].sort(key=lambda r: (order[r["decision"]], -(r.get("ev_interval") or (0, 0))[0]))
    out["collection"] = live.collection_health(conn, ledger_rows, T)
    if book_path and os.path.exists(book_path):
        os.remove(book_path)              # rebuilt deterministically from the immutable shards every time
    bconn = sqlite3.connect(book_path or ":memory:")
    ensure_schema(bconn)
    out["book"] = live_book.build(bconn, conn, T, load_rankings(rankings_dir), bettors=bettors)
    bconn.close()
    conn.close()
    return out


def build(as_of_iso, out_dir="data/site", features_path="data/derived/features.parquet", bettors=10_000,
          live_db=None, ledger_path=None, rankings_dir=None):
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    T = store.iso_to_epoch(as_of_iso)
    f = pd.read_parquet(features_path)
    up = upcoming_games(f, pd.Timestamp(as_of_iso))
    season = int(up.season.max())
    anchored = M.fit_season(f, season, "anchored")
    independent = M.fit_season(f, season, "independent")
    pa = {p["game_id"]: p for p in M.predict_games(anchored, up)}
    pi = {p["game_id"]: p for p in M.predict_games(independent, up)}

    # ---------------- DEMO market (simulated quotes around the real listed lines)
    mpath = os.path.join(out_dir, "demo_market.db")
    if os.path.exists(mpath):
        os.remove(mpath)
    mconn = store.connect(mpath)
    games = [{"event_id": r.game_id, "home": r.home_team, "away": r.away_team,
              "kickoff": r.kickoff_utc.timestamp(), "spread_line": r.spread_line, "total_line": r.total_line}
             for r in up.itertuples()]
    demo_market.build(mconn, games, T, anchored.margin_kn, anchored.margin.sigma)

    # ---------------- Engine A: synthetic sportsbook on the DEMO market
    bpath = os.path.join(out_dir, "synthetic_book.db")
    if os.path.exists(bpath):
        os.remove(bpath)
    bconn = sqlite3.connect(bpath)
    ensure_schema(bconn)
    pid = ensure_population(bconn, "nfl", sorted({f"nfl_{x.lower()}" for x in TEAMS}), bettor_count=bettors)
    # window ends at the last kickoff; the clock is advanced only to the snapshot time T
    slate = SlateSpec("nfl", f"site_{as_of_iso[:10]}", _iso(T - 5 * 86400), _iso(max(g["kickoff"] for g in games)),
                      tuple(GameSpec(g["event_id"], f"nfl_{g['home'].lower()}", f"nfl_{g['away'].lower()}",
                                     _iso(g["kickoff"]), False, False) for g in games))
    msrc = MarketV4Source(mconn, max_age_seconds=45 * 60, source_id="demo_sim:site")
    run_id = create_run(bconn, pid, slate, msrc)
    adv = advance(bconn, run_id, _iso(T), msrc, NoFacts(), data_complete_through=_iso(T))

    # ---------------- EV scanner (forward paper scan, DEMO market, not validated)
    lpath = os.path.join(out_dir, "paper_ledger.db")
    if os.path.exists(lpath):
        os.remove(lpath)
    lconn = ledger.connect(lpath)
    scan_rows = forward.scan(mconn, lconn, [{"event_id": g["event_id"], "kickoff": g["kickoff"], "home": g["home"],
                                             "away": g["away"]} for g in games], T,
                             anchored.margin_kn, anchored.margin.sigma, anchored.total_kn, anchored.total.sigma,
                             allow_demo=True)

    out_games = []
    for r in up.itertuples():
        gid = r.game_id
        ws = fetch_wagers(bconn, run_id, gid, market=None)
        need = settlement.book_need_all(ws, ref_home_spread=-float(r.spread_line), ref_total=float(r.total_line),
                                        max_score=55) if ws else None
        if need:   # trim bulky per-margin tables for the page
            need["spread"].pop("by_archetype", None)
        ms = {m: asof.market_state(mconn, gid, m, T) for m in ("spreads", "totals", "h2h")}
        sh = {m: signals.analyse(mconn, gid, m, T, allow_demo=True) for m in ("spreads", "totals")}
        tickets = {}
        for w in ws:
            k = f"{w['market']}:{w['side']}"
            tickets.setdefault(k, [0, 0.0])
            tickets[k][0] += 1
            tickets[k][1] += w["stake"]
        out_games.append({
            "game_id": gid, "season": int(r.season), "week": int(r.week),
            "home": r.home_team, "away": r.away_team, "home_name": TEAMS.get(r.home_team, r.home_team),
            "away_name": TEAMS.get(r.away_team, r.away_team), "kickoff": _iso(r.kickoff_utc.timestamp()),
            "listed": {"home_spread": -float(r.spread_line), "total": float(r.total_line),
                       "home_ml": None if pd.isna(r.home_moneyline) else int(r.home_moneyline),
                       "away_ml": None if pd.isna(r.away_moneyline) else int(r.away_moneyline),
                       "source": "nflverse games.csv listed line (approx. consensus; book/time unspecified)"},
            "sims": {"anchored": sims_block(pa[gid], r), "independent": sims_block(pi[gid], r)} if gid in pa else None,
            "market": ms, "sharp": sh, "need": need,
            "book_tickets": {k: {"tickets": v[0], "handle": round(v[1], 2)} for k, v in sorted(tickets.items())},
            "qb": {"home": getattr(r, "home_qb_name", None), "away": getattr(r, "away_qb_name", None)},
            "roof": r.roof, "rest": {"home": r.home_rest, "away": r.away_rest},
        })

    # ---------------- performance + health inputs produced elsewhere (real results)
    def _load(p):
        return json.load(open(p)) if os.path.exists(p) else None

    perf = {"engine_c": _load("docs_v4/engine_c_eval.json"), "closing_line": _load("docs_v4/backtest_closing_line.json"),
            "controls": _load("docs_v4/controls_synthetic.json")}
    if perf["engine_c"]:
        for v in perf["engine_c"]["variants"].values():
            v.pop("last_model_coefficients", None)
    manifest = _load("data/raw/MANIFEST.json") or {}
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = None
    health = {
        "build_seconds": None, "git_commit": commit, "as_of": as_of_iso,
        "versions": {"sims": "sims_v4.nfl.0.1", "edge": "edge_v4.0.1", "market": "market_v4.0.1",
                     "sharp": signals.__name__ and "sharp_v4.0.1-experimental", "sportsbook_engine": adv.get("run_id", "")[:12],
                     "decision_policy": ev.DecisionPolicy().version},
        "data_sources": [{"file": k, "bytes": v["bytes"], "sha256": v["sha256"][:12], "fetched_at": v["fetched_at"],
                          "licence": v["licence"]} for k, v in sorted(manifest.items()) if k == "games.csv"]
                        + ([{"file": f"play_by_play (2006-2026, {sum(1 for k in manifest if k.startswith('play_by_play'))} files)",
                             "bytes": sum(v['bytes'] for k, v in manifest.items() if k.startswith('play_by_play')),
                             "sha256": "per-file in MANIFEST.json",
                             "fetched_at": max((v['fetched_at'] for k, v in manifest.items() if k.startswith('play_by_play')), default=None),
                             "licence": next((v['licence'] for k, v in manifest.items() if k.startswith('play_by_play')), "")}]
                           if manifest else []),
        "odds_api": {"status": "NOT CONNECTED in this build (no dev server; provider host unreachable from sandbox)",
                     "v4_monthly_cap": 6000, "v4_daily_cap": 400, "v3_floor_remaining": 2000, "live_enabled": False},
        "v3_production": "untouched: no access from this environment",
        "demo_market": {"rows": mconn.execute("SELECT COUNT(*) FROM mkt_quote").fetchone()[0],
                        "gaps": quality.fetch_gaps(mconn, "americanfootball_nfl", 1800, T - 5 * 86400, T)},
        "synthetic_book": {"bettors": bettors, "wagers": adv["accepted"], "arrivals": adv["arrivals"],
                           "passes": adv["passes"]},
        "tests": _load("data/site/test_summary.json"),
    }
    lv = live_block(live_db, ledger_path, T, rankings_dir, os.path.join(out_dir, "live_book.db"),
                    bettors) if live_db else None
    if lv:
        # Odds API team names -> nflverse codes, so real sharp evidence can sit on the NFL game cards
        code = {v: k for k, v in TEAMS.items()}
        by_teams = {(code.get(r["home"]), code.get(r["away"]), r["market"]): r for r in lv["sharp"]
                    if r["sport"] == "americanfootball_nfl"}
        for g in out_games:
            for m in ("spreads", "totals"):
                hit = by_teams.get((g["home"], g["away"], m))
                if hit:
                    g["sharp_live"] = g.get("sharp_live", {})
                    g["sharp_live"][m] = hit
        health["odds_api"]["status"] = "CONNECTED through GitHub Actions (credit-gated collector)"
        health["odds_api"]["live_enabled"] = True
    snap = {"as_of": as_of_iso, "generated_at": _iso(time.time()), "season": season, "live": lv,
            "data_mode": "live" if lv else "demo",
            "games": out_games, "scanner": scan_rows, "plays": [],
            "plays_note": "No strategy has passed the evidence gate (rebuild plan §G). Official plays: none.",
            "performance": perf, "health": health,
            "cfb": {"status": "PENDING DATA", "detail": "CollegeFootballData API is unreachable from this build environment; "
                                                       "CFB Top-25 coverage starts once a CFB source is connected."}}
    snap["health"]["build_seconds"] = round(time.time() - t0, 1)
    with open(os.path.join(out_dir, "snapshot.json"), "w") as fh:
        json.dump(_clean(snap), fh, default=str)
    for c in (mconn, bconn, lconn):
        c.close()
    return snap


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default="2026-10-26T21:00:00Z")
    ap.add_argument("--out", default="data/site")
    ap.add_argument("--bettors", type=int, default=10_000)
    ap.add_argument("--features", default="data/derived/features.parquet")
    ap.add_argument("--live-db", default=None, help="market store rebuilt from collected shards (real odds)")
    ap.add_argument("--ledger", default=None, help="credit ledger (ledger.jsonl from release v4-state)")
    ap.add_argument("--rankings-dir", default=None, help="CFBD AP-poll snapshots (release cfb-rankings)")
    a = ap.parse_args(argv)
    s = build(a.as_of, a.out, a.features, bettors=a.bettors, live_db=a.live_db, ledger_path=a.ledger,
              rankings_dir=a.rankings_dir)
    print(f"snapshot: {len(s['games'])} games, {len(s['scanner'])} scanner rows, "
          f"{s['health']['synthetic_book']['wagers']} synthetic wagers, {s['health']['build_seconds']} s")


if __name__ == "__main__":
    main()
