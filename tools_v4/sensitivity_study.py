"""
tools_v4/sensitivity_study.py -- why is the fixture Need almost always the underdog?

Answers the question with three kinds of evidence, all on SYNTHETIC fixtures
(no real market data; nothing here validates the model against reality):

1. EXACT ATTRIBUTION. Book P&L is linear in wagers, so for every game the
   "favourite pressure"
        FP = (P&L if underdog covers every number - P&L if favourite covers every number) / handle
   splits exactly into one additive term per archetype. FP > 0 means the
   book needs the underdog. Archetype terms show WHO creates the pressure.

2. BEHAVIOURAL SWEEPS (market held fixed): favourite preference, high-stakes
   concentration, archetype weights, timing, price sensitivity, book lag,
   and a behaviour-neutral control (no directional preferences at all).

3. MARKET COUNTERFACTUALS (behaviour held fixed): lines frozen at the open,
   every move mirrored toward the underdog, retail books not lagging / lagging
   longer, and a mirrored symmetric slate -- each also under the
   behaviour-neutral control.

If the Need tracks (3) it responds to observed market conditions; if it only
tracks (2) it is an expression of behavioural assumptions.

Common random numbers: runs differ only by the variant (sportsbook_v4.engine
rng_key excludes params and market source), so variant-vs-baseline deltas are
not re-rolled noise.

Usage: python3 tools_v4/sensitivity_study.py [--seeds 4] [--bettors 10000]
Writes docs_v4/sensitivity_m2.json and docs_v4/sensitivity_m2.md
"""

import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sportsbook_v4.engine as eng  # noqa: E402
import sportsbook_v4.params as P  # noqa: E402
import sportsbook_v4.population as pop  # noqa: E402
from sportsbook_v4.facts import NoFacts  # noqa: E402
from sportsbook_v4.ledger_read import fetch_wagers  # noqa: E402
from sportsbook_v4.market import representative_offer  # noqa: E402
from sportsbook_v4.settlement import exposure  # noqa: E402
from tests_v4 import fixtures as fx  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs_v4"


def wilson(k, n, z=1.96):
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(c - h, 3), round(c + h, 3)


def scaled_fav(factor):
    return {a: {"fav_pref": (spec["fav_pref"][0] * factor, spec["fav_pref"][1],
                             min(spec["fav_pref"][2], spec["fav_pref"][0] * factor),
                             max(spec["fav_pref"][3], spec["fav_pref"][0] * factor))}
            for a, spec in P.ARCHETYPES.items()}


def shares(**changes):
    """Move share between named archetypes and REC_CASUAL so the total stays 1."""
    out, delta = {}, 0.0
    for arch, share in changes.items():
        delta += share - P.ARCHETYPES[arch]["share"]
        out[arch] = {"share": share}
    out["REC_CASUAL"] = {"share": round(P.ARCHETYPES["REC_CASUAL"]["share"] - delta, 10)}
    return out


NEUTRAL = {"fav_pref": (0.0, 0.0, 0.0, 0.0), "popularity_weight": (0.0, 0.0, 0.0, 0.0),
           "recency_weight": (0.0, 0.0, 0.0, 0.0), "home_pref": (0.0, 0.0, 0.0, 0.0), "loyalty": 0.0}

# name -> (group, kwargs for params.variant, market key)
VARIANTS = {
    "baseline": ("baseline", {}, "moving"),
    # favourite preference (every archetype's mean scaled)
    "fav_pref_x0": ("favorite_preference", {"archetype_overrides": scaled_fav(0.0)}, "moving"),
    "fav_pref_x0.5": ("favorite_preference", {"archetype_overrides": scaled_fav(0.5)}, "moving"),
    "fav_pref_x1.5": ("favorite_preference", {"archetype_overrides": scaled_fav(1.5)}, "moving"),
    "fav_pref_x-1": ("favorite_preference", {"archetype_overrides": scaled_fav(-1.0)}, "moving"),
    # high-stakes concentration
    "hs_share_0": ("high_stakes", {"archetype_overrides": shares(HIGH_STAKES_REC=0.0)}, "moving"),
    "hs_share_8pct": ("high_stakes", {"archetype_overrides": shares(HIGH_STAKES_REC=0.08)}, "moving"),
    "hs_bankroll_10k": ("high_stakes", {"archetype_overrides": {"HIGH_STAKES_REC": {"bankroll_median": 10000.0}}}, "moving"),
    "hs_bankroll_100k": ("high_stakes", {"archetype_overrides": {"HIGH_STAKES_REC": {"bankroll_median": 100000.0}}}, "moving"),
    "hs_fav_pref_0": ("high_stakes", {"archetype_overrides": {"HIGH_STAKES_REC": {"fav_pref": (0.0, 0.3, -0.5, 1.2)}}}, "moving"),
    "hs_fav_pref_-0.4": ("high_stakes", {"archetype_overrides": {"HIGH_STAKES_REC": {"fav_pref": (-0.4, 0.3, -1.2, 1.2)}}}, "moving"),
    # archetype weights
    "sharp_share_0": ("archetype_weights", {"archetype_overrides": shares(SHARP=0.0)}, "moving"),
    "sharp_share_6pct": ("archetype_weights", {"archetype_overrides": shares(SHARP=0.06)}, "moving"),
    "underdog_share_16pct": ("archetype_weights", {"archetype_overrides": shares(UNDERDOG_CONTRARIAN=0.16)}, "moving"),
    "no_team_loyal": ("archetype_weights", {"archetype_overrides": shares(TEAM_LOYAL=0.0)}, "moving"),
    # timing
    "timing_all_steady": ("timing", {"all_archetypes": {"timing": "steady"}}, "moving"),
    "timing_sharps_late": ("timing", {"archetype_overrides": {"SHARP": {"timing": "weekend_late"}}}, "moving"),
    "timing_recs_early": ("timing", {"archetype_overrides": {a: {"timing": "early_week"} for a in
                                     ("REC_CASUAL", "TEAM_LOYAL", "NARRATIVE_CHASER", "HIGH_STAKES_REC")}}, "moving"),
    # price sensitivity
    "price_max_-110": ("price_sensitivity", {"all_archetypes": {"max_implied_prob": 0.5239}}, "moving"),
    "price_max_-150": ("price_sensitivity", {"all_archetypes": {"max_implied_prob": 0.60}}, "moving"),
    "selective_no_shopping": ("price_sensitivity", {"archetype_overrides": {"SELECTIVE_PRICE_SENSITIVE": {"shops": False}}}, "moving"),
    "reference_all_books": ("price_sensitivity", {"book_overrides": {"reference_books": []}}, "moving"),
    # book offer lag
    "lag_0": ("market_lag", {"book_overrides": {"offer_lag_seconds": 0}}, "moving"),
    "lag_30m": ("market_lag", {"book_overrides": {"offer_lag_seconds": 1800}}, "moving"),
    "lag_1h": ("market_lag", {"book_overrides": {"offer_lag_seconds": 3600}}, "moving"),
    "lag_2h": ("market_lag", {"book_overrides": {"offer_lag_seconds": 7200}}, "moving"),
    # behaviour-neutral control
    "neutral_behaviour": ("neutral_control", {"all_archetypes": NEUTRAL}, "moving"),
    # market counterfactuals (baseline behaviour)
    "mkt_static": ("market_counterfactual", {}, "static"),
    "mkt_reversed": ("market_counterfactual", {}, "reversed"),
    "mkt_retail_lag_0": ("market_counterfactual", {}, "retail_lag_0"),
    "mkt_retail_lag_6h": ("market_counterfactual", {}, "retail_lag_6h"),
    "mkt_symmetric": ("market_counterfactual", {}, "symmetric"),
    # market counterfactuals under the behaviour-neutral control
    "neutral_mkt_static": ("neutral_x_market", {"all_archetypes": NEUTRAL}, "static"),
    "neutral_mkt_reversed": ("neutral_x_market", {"all_archetypes": NEUTRAL}, "reversed"),
    "neutral_mkt_symmetric": ("neutral_x_market", {"all_archetypes": NEUTRAL}, "symmetric"),
}

MARKETS = {
    "moving": lambda: (fx.NFL_WEEK, fx.build_quotes(fx.NFL_WEEK, source_id="fx:moving")),
    "static": lambda: (fx.static_week(), fx.build_quotes(fx.static_week(), source_id="fx:static")),
    "reversed": lambda: (fx.reversed_week(), fx.build_quotes(fx.reversed_week(), source_id="fx:reversed")),
    "retail_lag_0": lambda: (fx.NFL_WEEK, fx.build_quotes(fx.NFL_WEEK, retail_lag=fx.timedelta(0),
                                                          source_id="fx:retail_lag_0")),
    "retail_lag_6h": lambda: (fx.NFL_WEEK, fx.build_quotes(fx.NFL_WEEK, retail_lag=fx.timedelta(hours=6),
                                                           source_id="fx:retail_lag_6h")),
    "symmetric": lambda: (fx.symmetric_week(), fx.build_quotes(fx.symmetric_week(), source_id="fx:symmetric")),
}


def closing_favorite(market, game):
    offer = representative_offer(market.latest_quotes(game.canonical_game_id, eng.to_epoch(game.commence_time) - 1))
    if offer is None or offer["home_spread"] == 0:
        return None
    return "home" if offer["home_spread"] < 0 else "away"


def run_variant(name, seeds, bettors):
    group, kwargs, market_key = VARIANTS[name]
    rows = []
    with P.variant(name, **kwargs):
        for s in range(seeds):
            week, market = MARKETS[market_key]()
            slate = fx.build_slate(week, slate_id=f"{market_key}_s{s}" if market_key == "symmetric" else f"nfl_s{s}")
            conn = fx.new_v4_db()
            pid = pop.ensure_population(conn, "nfl", fx.NFL_TEAMS, bettor_count=bettors)
            run_id = eng.create_run(conn, pid, slate, market)
            eng.advance(conn, run_id, fx.WINDOW_END, market, NoFacts(), fx.WINDOW_END)
            for g in slate.games:
                e = exposure(fetch_wagers(conn, run_id, g.canonical_game_id))
                fav = closing_favorite(market, g)
                row = {"seed": s, "game": g.canonical_game_id, "closing_favorite": fav,
                       "need": e.get("financial_need_side"), "handle": e["handle"]}
                if fav and e["handle"]:
                    dog = "away" if fav == "home" else "home"
                    pnl = {"home": "pnl_if_home_covers_all", "away": "pnl_if_away_covers_all"}
                    row["need_rel"] = ("favorite" if e["financial_need_side"] == fav else
                                       "underdog" if e["financial_need_side"] == dog else "tie")
                    row["fav_pressure"] = (e[pnl[dog]] - e[pnl[fav]]) / e["handle"]
                    row["by_archetype"] = {a: (v[pnl[dog]] - v[pnl[fav]]) / e["handle"]
                                           for a, v in e["by_archetype"].items()}
                    row["handle_share"] = {a: v["handle"] / e["handle"] for a, v in e["by_archetype"].items()}
                rows.append(row)
    return group, market_key, rows


def summarize(rows):
    rel = [r for r in rows if "need_rel" in r]
    dog = sum(r["need_rel"] == "underdog" for r in rel)
    fav = sum(r["need_rel"] == "favorite" for r in rel)
    n = dog + fav
    archs = sorted({a for r in rel for a in r["by_archetype"]})
    attr = {a: round(sum(r["by_archetype"].get(a, 0.0) for r in rel) / len(rel), 4) for a in archs} if rel else {}
    hshare = {a: round(sum(r["handle_share"].get(a, 0.0) for r in rel) / len(rel), 4) for a in archs} if rel else {}
    lo, hi = wilson(dog, n)
    return {
        "game_seeds_with_favorite": len(rel), "underdog_needs": dog, "favorite_needs": fav,
        "underdog_need_share": round(dog / n, 3) if n else None, "underdog_share_95ci": [lo, hi],
        "mean_favorite_pressure": round(sum(r["fav_pressure"] for r in rel) / len(rel), 4) if rel else None,
        "attribution_mean_per_archetype": attr, "handle_share_mean_per_archetype": hshare,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--bettors", type=int, default=10_000)
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    report = {"label": "SYNTHETIC FIXTURES ONLY -- UNVALIDATED PARAMETERS -- NOT MARKET EVIDENCE",
              "params_version": P.PARAMS_VERSION, "seeds": args.seeds, "bettors": args.bettors,
              "favorite_pressure_definition": "(pnl_if_underdog_covers_all - pnl_if_favorite_covers_all) / handle; "
                                              ">0 means the book needs the underdog",
              "variants": {}}
    t0 = time.time()
    for name in (args.only or VARIANTS):
        group, market_key, rows = run_variant(name, args.seeds, args.bettors)
        report["variants"][name] = {"group": group, "market": market_key, **summarize(rows), "rows": rows}
        v = report["variants"][name]
        print(f"{name:24s} {group:22s} {market_key:14s} dog {v['underdog_needs']:2d}/{v['underdog_needs'] + v['favorite_needs']:2d} "
              f"FP {v['mean_favorite_pressure']:+.4f}  ({time.time() - t0:.0f}s)", flush=True)
    (OUT / "sensitivity_m2.json").write_text(json.dumps(report, indent=1, default=str))
    write_markdown(report)


def write_markdown(report):
    v = report["variants"]
    base = v.get("baseline")
    archs = sorted(base["attribution_mean_per_archetype"]) if base else []
    lines = [
        "# V4 sensitivity study (generated)", "",
        f"{report['label']}. Seeds per variant: {report['seeds']}; bettors: {report['bettors']}. "
        "Favourite pressure (FP) = (P&L if underdog covers every number − P&L if favourite covers every number) / handle; "
        "FP > 0 means the book needs the underdog.", "",
        "| Variant | Group | Market | Underdog Needs | 95% CI | Mean FP | Δ FP vs baseline |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, r in v.items():
        n = r["underdog_needs"] + r["favorite_needs"]
        d = "" if not base or name == "baseline" else f"{r['mean_favorite_pressure'] - base['mean_favorite_pressure']:+.4f}"
        lines.append(f"| {name} | {r['group']} | {r['market']} | {r['underdog_needs']}/{n} | "
                     f"{r['underdog_share_95ci'][0]}–{r['underdog_share_95ci'][1]} | {r['mean_favorite_pressure']:+.4f} | {d} |")
    if base:
        lines += ["", "## Exact archetype attribution of favourite pressure (mean FP contribution; sums to total FP)", "",
                  "| Variant | " + " | ".join(archs) + " |", "|---|" + "---|" * len(archs)]
        for name in ("baseline", "neutral_behaviour", "mkt_static", "mkt_reversed", "neutral_mkt_static",
                     "neutral_mkt_reversed", "hs_share_0", "sharp_share_0", "fav_pref_x0"):
            if name in v:
                lines.append(f"| {name} | " + " | ".join(f"{v[name]['attribution_mean_per_archetype'].get(a, 0):+.4f}"
                                                         for a in archs) + " |")
        lines += ["", "## Mean handle share per archetype (baseline)", "",
                  "| " + " | ".join(archs) + " |", "|" + "---|" * len(archs),
                  "| " + " | ".join(f"{base['handle_share_mean_per_archetype'].get(a, 0):.3f}" for a in archs) + " |"]
    (OUT / "sensitivity_m2.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
