"""
tools_v4/run_diagnostics.py -- V4 model diagnostics on deterministic fixtures.

Reports, for the SYNTHETIC fixture weeks only (not real market data):
  * archetype mix: bettors, tickets, handle share
  * stake distribution per archetype
  * per-game ticket vs handle split, Need side, swing (seed 0)
  * Need-side frequencies across many seeds (home/away, favourite/underdog)
  * sensitivity of those frequencies to key UNVALIDATED assumptions

Everything runs against throw-away in-memory databases.

Usage:
    python3 tools_v4/run_diagnostics.py [--seeds 12] [--bettors 10000] [--out diagnostics.json]
"""

import argparse
import copy
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sportsbook_v4.engine as eng  # noqa: E402
import sportsbook_v4.params as P  # noqa: E402
import sportsbook_v4.population as pop  # noqa: E402
from sportsbook_v4.facts import NoFacts  # noqa: E402
from sportsbook_v4.ledger_read import fetch_wagers  # noqa: E402
from sportsbook_v4.settlement import exposure  # noqa: E402
from tests_v4 import fixtures as fx  # noqa: E402


@contextmanager
def patched(archetype_overrides=None, book_overrides=None):
    """Temporarily change UNVALIDATED parameters (diagnostics only)."""
    saved_a, saved_b = copy.deepcopy(P.ARCHETYPES), copy.deepcopy(P.BOOK)
    try:
        for arch, changes in (archetype_overrides or {}).items():
            P.ARCHETYPES[arch].update(changes)
        P.BOOK.update(book_overrides or {})
        yield
    finally:
        P.ARCHETYPES.clear(); P.ARCHETYPES.update(saved_a)
        P.BOOK.clear(); P.BOOK.update(saved_b)


def run_week(week, slate_id, bettors, facts=None):
    conn = fx.new_v4_db()
    pid = pop.ensure_population(conn, "nfl", fx.NFL_TEAMS, bettor_count=bettors)
    market, slate = fx.build_quotes(week), fx.build_slate(week, slate_id=slate_id)
    run_id = eng.create_run(conn, pid, slate, market)
    summary = eng.advance(conn, run_id, fx.WINDOW_END, market, facts or NoFacts(), fx.WINDOW_END)
    games = {}
    for g in slate.games:
        e = exposure(fetch_wagers(conn, run_id, g.canonical_game_id))
        closing = market.latest_quotes(g.canonical_game_id, eng.to_epoch(g.commence_time) - 60)
        hs = sorted(q.home_spread for q in closing)[(len(closing) - 1) // 2] if closing else None
        fav = "home" if hs is not None and hs < 0 else ("away" if hs is not None and hs > 0 else None)
        games[g.canonical_game_id] = {"exposure": e, "closing_favorite": fav}
    return conn, run_id, summary, games


def need_frequencies(week, seeds, bettors, label, facts=None):
    counts = {"home": 0, "away": 0, "tie_or_none": 0, "favorite": 0, "underdog": 0, "pickem": 0}
    for s in range(seeds):
        _, _, _, games = run_week(week, f"{label}_seed{s}", bettors, facts)
        for g in games.values():
            side = g["exposure"].get("financial_need_side")
            if side not in ("home", "away"):
                counts["tie_or_none"] += 1
                continue
            counts[side] += 1
            if g["closing_favorite"] is None:
                counts["pickem"] += 1
            else:
                counts["favorite" if side == g["closing_favorite"] else "underdog"] += 1
    n = sum(counts[k] for k in ("home", "away", "tie_or_none"))
    counts["games_evaluated"] = n
    counts["home_need_share"] = round(counts["home"] / n, 3) if n else None
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=12)
    ap.add_argument("--bettors", type=int, default=10_000)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    report = {"label": "SYNTHETIC FIXTURES ONLY -- UNVALIDATED PARAMETERS", "params_version": P.PARAMS_VERSION,
              "bettors": args.bettors, "seeds": args.seeds}

    conn, run_id, summary, games = run_week(fx.NFL_WEEK, "diag_seed0", args.bettors, fx.build_facts())
    report["seed0_interval"] = {k: summary[k] for k in ("arrivals", "accepted", "passes")}
    wagers = fetch_wagers(conn, run_id)
    bettors = pop.load_population(conn, conn.execute("SELECT population_id FROM v4_population").fetchone()[0])["bettors"]
    total_handle = sum(w["stake"] for w in wagers)
    arch = {}
    for b in bettors:
        arch.setdefault(b["archetype"], {"bettors": 0, "tickets": 0, "handle": 0.0, "stakes": []})["bettors"] += 1
    for w in wagers:
        a = arch[w["archetype"]]
        a["tickets"] += 1
        a["handle"] += w["stake"]
        a["stakes"].append(w["stake"])
    report["archetypes"] = {
        k: {"bettors": v["bettors"], "tickets": v["tickets"],
            "ticket_share": round(v["tickets"] / len(wagers), 4),
            "handle_share": round(v["handle"] / total_handle, 4),
            "stake_median": median(v["stakes"]) if v["stakes"] else None,
            "stake_p90": sorted(v["stakes"])[int(0.9 * len(v["stakes"]))] if v["stakes"] else None,
            "stake_max": max(v["stakes"]) if v["stakes"] else None,
            "tickets_per_bettor": round(v["tickets"] / v["bettors"], 3)}
        for k, v in sorted(arch.items())
    }
    report["seed0_games"] = {
        gid: {k: g["exposure"].get(k) for k in ("tickets", "handle", "home_ticket_pct", "home_handle_pct",
                                                 "financial_need_side", "pnl_if_home_covers_all",
                                                 "pnl_if_away_covers_all", "pnl_swing", "worst_case_pnl")}
             | {"distinct_accepted_numbers": len(g["exposure"].get("accepted_numbers", [])),
                "closing_favorite": g["closing_favorite"]}
        for gid, g in games.items()
    }

    report["need_frequencies"] = {
        "nfl_week_fixture": need_frequencies(fx.NFL_WEEK, args.seeds, args.bettors, "nfl", fx.build_facts()),
        "symmetric_fixture": need_frequencies(fx.symmetric_week(), args.seeds, args.bettors, "sym"),
    }

    sens = {}
    variants = {
        "baseline": ({}, {}),
        "offer_lag_0s": ({}, {"offer_lag_seconds": 0}),
        "offer_lag_1h": ({}, {"offer_lag_seconds": 3600}),
        "rec_fav_pref_0": ({"REC_CASUAL": {"fav_pref": (0.0, 0.3, -0.5, 1.5)}}, {}),
        "rec_fav_pref_1": ({"REC_CASUAL": {"fav_pref": (1.0, 0.3, -0.5, 1.5)}}, {}),
        "hs_risk_half": ({"HIGH_STAKES_REC": {"weekly_risk_fraction": 0.125}}, {}),
    }
    for name, (ao, bo) in variants.items():
        with patched(ao, bo):
            sens[name] = need_frequencies(fx.NFL_WEEK, max(3, args.seeds // 3), args.bettors, f"sens_{name}",
                                          fx.build_facts())
    report["sensitivity"] = sens

    text = json.dumps(report, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
