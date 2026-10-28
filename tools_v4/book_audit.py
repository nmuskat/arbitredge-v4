"""
tools_v4/book_audit.py -- independent audit of the persisted synthetic-book ledger (MODELED data).

Rebuilds the book from the immutable v4-book files (integrity-checked by sportsbook_v4.persist)
and the collected odds shards, then checks, without reusing the code under audit where possible:

  A. population identity: 10,000 persistent bettors per sport regenerate byte-identically;
     archetype mix, bankrolls and traits per archetype
  B. timing: when bettors arrive (weekday x hour, Central time) and how often
  C. decisions: why each arrival bet or passed; market / side / stake by archetype
  D. prices: every accepted wager's line and price equal the real representative market quote
     at its offer time (no repricing, no invented prices)
  E. accounting: tickets, handle, ticket % / handle %, potential payout, liability and Need per
     game / market / side recomputed from raw wager rows and compared with the dashboard figures

  python3 tools_v4/book_audit.py --shards <dir> --book <dir> [--out audit.json]
"""

import argparse
import collections
import glob
import json
import math
import os
import sqlite3
import statistics
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis_v4.actions_analyze import rebuild  # noqa: E402
from sportsbook_v4 import ENGINE_VERSION, live_book as LB, persist  # noqa: E402
from sportsbook_v4.engine import bettor_arrivals, load_run  # noqa: E402
from sportsbook_v4.market import representative_ml_offer, representative_offer, representative_total_offer  # noqa: E402
from sportsbook_v4.params import ARCHETYPES, BOOK  # noqa: E402
from sportsbook_v4.population import load_population  # noqa: E402
from sportsbook_v4.schema import ensure_schema  # noqa: E402
from sportsbook_v4.util import to_epoch  # noqa: E402

CT = ZoneInfo("America/Chicago")
WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def population_section(book, run):
    pop = load_population(book, run["population_id"], verify=True)      # raises if the hash does not match
    bs = pop["bettors"]
    by = collections.defaultdict(list)
    for b in bs:
        by[b["archetype"]].append(b)
    out = {"bettors": len(bs), "unique_ids": len({b["bettor_uid"] for b in bs}), "hash_verified": True,
           "archetypes": {}}
    for a, xs in sorted(by.items()):
        br = [b["bankroll"] for b in xs]
        out["archetypes"][a] = {
            "count": len(xs), "share_spec": ARCHETYPES[a]["share"],
            "bankroll_median": round(statistics.median(br), 2), "bankroll_p10": round(_q(br, 0.1), 2),
            "bankroll_p90": round(_q(br, 0.9), 2),
            "arrivals_per_week_mean": round(statistics.mean(b["traits"]["arrivals_per_week"] for b in xs), 2),
            "fav_pref_mean": round(statistics.mean(b["traits"]["fav_pref"] for b in xs), 3),
            "over_pref_mean": round(statistics.mean(b["traits"]["over_pref"] for b in xs), 3),
            "unit_fraction_mean": round(statistics.mean(b["traits"]["unit_fraction"] for b in xs), 4),
            "loyal_team_set": sum(1 for b in xs if b["traits"].get("loyal_team")),
            "timing_profile": ARCHETYPES[a]["timing"]}
    return out, pop


def timing_section(run, pop):
    ws, we = to_epoch(run["slate"].window_start), to_epoch(run["slate"].window_end)
    cache, by_day, by_hour, per_arch, n = {}, collections.Counter(), collections.Counter(), collections.Counter(), 0
    for b in pop["bettors"]:
        ts = bettor_arrivals(run["rng_key"], b, ws, we, cache)
        per_arch[b["archetype"]] += len(ts)
        for t in ts:
            d = datetime.fromtimestamp(t, CT)
            by_day[WEEKDAY[d.weekday()]] += 1
            by_hour[d.hour // 4 * 4] += 1
            n += 1
    return {"arrivals_in_week": n, "by_weekday_central": {d: by_day[d] for d in WEEKDAY},
            "by_4h_band_central": {f"{h:02d}-{h + 4:02d}": by_hour[h] for h in range(0, 24, 4)},
            "per_archetype": dict(per_arch)}


def decisions_section(book, run_id, wagers):
    passes = collections.Counter()
    arrivals = 0
    for pj, a in book.execute("SELECT passes_json, arrivals FROM v4_interval WHERE run_id=?", (run_id,)):
        arrivals += a
        passes.update(json.loads(pj))
    by = collections.defaultdict(list)
    for w in wagers:
        by[w["archetype"]].append(w)
    arch = {}
    for a, ws in sorted(by.items()):
        fav = 0
        n_side = 0
        for w in ws:
            if w["market"] == "spread":
                n_side += 1
                fav += (w["side"] == "home") == (w["accepted_home_spread"] < 0) and w["accepted_home_spread"] != 0
        arch[a] = {"wagers": len(ws), "market_mix": dict(collections.Counter(w["market"] for w in ws)),
                   "spread_on_favourite_pct": round(100 * fav / n_side, 1) if n_side else None,
                   "over_pct": round(100 * sum(w["side"] == "over" for w in ws) /
                                     max(1, sum(w["market"] == "total" for w in ws)), 1),
                   "stake_median": statistics.median(w["stake"] for w in ws),
                   "stake_p90": _q([w["stake"] for w in ws], 0.9), "handle": round(sum(w["stake"] for w in ws), 2)}
    return {"arrivals": arrivals, "accepted": len(wagers), "passes": dict(passes.most_common()),
            "by_archetype": arch}


def prices_section(live, run, wagers):
    """Each wager's line and price must equal the representative real quote at its offer time."""
    market = LB._market(live, run["slate"].sport)
    rep = {"spread": representative_offer, "total": representative_total_offer, "moneyline": representative_ml_offer}
    bad, checked, max_age = [], 0, 0
    for w in wagers:
        t = to_epoch(w["offer_as_of"])
        quotes = market.latest_quotes(w["canonical_game_id"], t)
        offer = rep[w["market"]](quotes)
        checked += 1
        if offer is None:
            bad.append((w["wager_id"], "no market at offer time"))
            continue
        # engine m4: after respected action the book deals the market makers' quote (mode on the wager)
        if json.loads(w["decision_json"]).get("book_mode") == "reference":
            offer = rep[w["market"]](quotes, books=BOOK["reference_books"])
        if w["market"] == "spread":
            hs = offer["home_spread"]
            line, price = (hs if w["side"] == "home" else -hs), offer["home_price" if w["side"] == "home" else "away_price"]
        elif w["market"] == "total":
            line, price = offer["total"], offer["over_price" if w["side"] == "over" else "under_price"]
        else:
            line, price = 0.0, offer["home_price" if w["side"] == "home" else "away_price"]
        if float(line) != float(w["accepted_line"]) or float(price) != float(w["accepted_price"]):
            bad.append((w["wager_id"], f"stored {w['accepted_line']}/{w['accepted_price']} vs market {line}/{price}"))
        newest = max(q.effective_epoch for q in quotes)
        max_age = max(max_age, to_epoch(w["accepted_at"]) - newest)
        if to_epoch(w["accepted_at"]) - t != BOOK["offer_lag_seconds"] and \
                abs(to_epoch(w["accepted_at"]) - t - BOOK["offer_lag_seconds"]) >= BOOK["decision_time_granularity_seconds"]:
            bad.append((w["wager_id"], "offer time is not accepted time minus the book's offer lag"))
    return {"checked": checked, "mismatches": len(bad), "examples": bad[:5],
            "max_quote_age_at_acceptance_h": round(max_age / 3600, 2)}


def _payout(w):
    p = float(w["accepted_price"])
    return w["stake"] + (w["stake"] * p / 100 if p > 0 else w["stake"] * 100 / -p)


def dashboard_figures(wagers, sport):
    """What the dashboard shows for this run's games: the same functions site-build uses."""
    from sportsbook_v4.settlement import book_need_all
    per = collections.defaultdict(list)
    for w in wagers:
        per[w["canonical_game_id"]].append(w)
    return [{"event_id": gid, "splits": LB.splits(ws),
             "need": LB._need_summary(book_need_all(ws, max_score=LB.MAX_SCORE[sport]))} for gid, ws in per.items()]


def accounting_section(wagers, dashboard_games):
    """Independent recomputation from raw wager rows vs the dashboard's splits / liability / Need."""
    sides = {"spread": ("home", "away"), "total": ("over", "under"), "moneyline": ("home", "away")}
    by = collections.defaultdict(list)
    for w in wagers:
        by[(w["canonical_game_id"], w["market"])].append(w)
    diffs, checked = [], 0
    dash = {g["event_id"]: g for g in dashboard_games}
    for (gid, m), ws in by.items():
        g = dash.get(gid)
        if g is None:
            diffs.append((gid, m, "game missing from dashboard"))
            continue
        row = g["splits"][m]
        n, h = len(ws), sum(w["stake"] for w in ws)
        a, b = sides[m]
        for s in (a, b):
            sw = [w for w in ws if w["side"] == s]
            st = sum(w["stake"] for w in sw)
            pay = sum(_payout(w) for w in sw)
            # book P&L if every wager on side s wins (pushes ignored: integer-line edge cases excluded)
            liab = round(h - st - (pay - st), 2)
            d = row["sides"][s]
            checked += 1
            for name, mine, theirs in (("tickets", len(sw), d["tickets"]), ("handle", round(st, 2), d["handle"]),
                                       ("ticket_pct", round(100 * len(sw) / n, 1), d["ticket_pct"]),
                                       ("handle_pct", round(100 * st / h, 1), d["handle_pct"]),
                                       ("potential_payout", round(pay, 2), d.get("potential_payout")),
                                       ("book_pnl_if_wins", liab, d.get("book_pnl_if_side_wins"))):
                if theirs is None or abs(mine - theirs) > 0.011:
                    diffs.append((gid[:8], m, s, name, mine, theirs))
        need = (g.get("need") or {}).get(m)
        if need and need.get("financial_need_side"):
            pa = row["sides"][a].get("book_pnl_if_side_wins")
            pb = row["sides"][b].get("book_pnl_if_side_wins")
            mine = None if pa == pb else (a if pa > pb else b)
            if mine != need["financial_need_side"]:
                diffs.append((gid[:8], m, "need", mine, need["financial_need_side"]))
    return {"side_rows_checked": checked, "mismatches": len(diffs), "examples": diffs[:10]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", required=True)
    ap.add_argument("--book", required=True)
    ap.add_argument("--rankings-dir", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    live, _ = rebuild(sorted(glob.glob(os.path.join(a.shards, "**", "quotes_*.jsonl.gz"), recursive=True)))
    book = sqlite3.connect(":memory:")
    ensure_schema(book)
    loaded = persist.load_dir(book, a.book)
    # the dashboard's own figures for comparison (same code path as site-build, no new ledger files)
    from web_v4.live_site import load_rankings
    snaps = load_rankings(a.rankings_dir)
    dash_book = sqlite3.connect(":memory:")
    ensure_schema(dash_book)
    last = max(to_epoch(r["interval"]) for r in
               [{"interval": x} for x in [book.execute("SELECT MAX(interval_end) FROM v4_interval").fetchone()[0]]])
    dash = LB.build(dash_book, live, last + LB.DATA_SAFETY_S, snaps, bettors=10_000, variants={}, book_in=a.book)
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "label": "MODELED", "runs": {}}
    for rid, info in loaded.items():
        run = load_run(book, rid)
        sport = run["slate"].sport
        cols = [c[0] for c in book.execute("SELECT * FROM v4_wager LIMIT 0").description]
        wagers = [dict(zip(cols, r)) for r in book.execute("SELECT * FROM v4_wager WHERE run_id=? ORDER BY rowid",
                                                           (rid,))]
        pop_s, pop = population_section(book, run)
        report["runs"][rid[:12]] = {
            "sport": sport, "slate_id": run["slate"].slate_id, "ledger_files": info["files"],
            "watermark": info["watermark"], "population": pop_s, "timing": timing_section(run, pop),
            "decisions": decisions_section(book, rid, wagers),
            "prices": (prices_section(live, run, wagers) if run["engine_version"] == ENGINE_VERSION else
                       {"skipped": f"priced by earlier engine {run['engine_version']} (superseded run); "
                                   "its prices are checked with that engine's offer rules, not these"}),
            "accounting": accounting_section(wagers, dashboard_figures(wagers, sport)),
            "shown_on_dashboard": (dash[sport].get("audit") or {}).get("run_id") == rid,
            "bettors_with_wagers": len({w["bettor_index"] for w in wagers}),
            "bettors_with_2plus": sum(1 for c in collections.Counter(w["bettor_index"] for w in wagers).values()
                                      if c >= 2)}
    js = json.dumps(report, indent=1, default=str)
    if a.out:
        open(a.out, "w").write(js)
    print(js)


if __name__ == "__main__":
    main()
