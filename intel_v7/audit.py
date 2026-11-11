"""
intel_v7/audit.py -- market-data quality audit of the collected odds (read-only).

  python -m intel_v7.audit --market-db market.db --out audit.json

Reports, per sport and book: coverage of fetches, usable two-way pairs, margins; per era (historical backfill vs live
collection): fetch intervals; provider refresh lag (market_last_update) and how often a refresh carries no price
change; BetOnline vs LowVig identity; the observed resolution of price-change times; kickoff/team-name changes.
"""
import argparse
import collections
import json
import sqlite3
import statistics as st

SPORTS = {"americanfootball_nfl": "nfl", "americanfootball_ncaaf": "cfb"}


def q(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 2) if xs else None


def audit(conn):
    out = {"eras": {}, "books": {}, "operator_identity": {}, "change_resolution": {}, "identity": {}}
    rows = conn.execute("SELECT event_id, sport, commence, home, away, book, market, side, point, price, "
                        "market_last_update, fetched_at, source FROM mkt_quote WHERE market IN ('spreads','totals')")
    fetches = collections.defaultdict(set)            # (sport, event) -> fetch times
    book_f = collections.defaultdict(set)             # (sport, book, event) -> fetch times
    pairs = collections.defaultdict(dict)             # (sport, event, market, book, fetched) -> {side: (pt, pr)}
    lag = collections.defaultdict(list)
    era_of = {}
    names = collections.defaultdict(set)
    kick = collections.defaultdict(set)
    lu_seq = collections.defaultdict(list)            # (event, market, book) -> [(fetched, last_update, pt, pr, side)]
    for e, sp, cm, h, a, bk, m, sd, pt, pr, lu, fa, src in rows:
        sp = SPORTS.get(sp, sp)
        if fa >= cm:
            continue
        era = "historical backfill" if "historical" in src else "live collection"
        era_of[(sp, e, fa)] = era
        fetches[(sp, e)].add(fa)
        book_f[(sp, bk, e)].add(fa)
        pairs[(sp, e, m, bk, fa)][sd] = (pt, pr)
        lag[era].append((fa - lu) / 60)
        names[(sp, e)].add((h, a))
        kick[(sp, e)].add(cm)
        if sd in ("home", "over"):
            lu_seq[(e, m, bk)].append((fa, lu, pt, pr))
    # eras: fetch interval per event
    for era in ("historical backfill", "live collection"):
        gaps, last6 = [], []
        for (sp, e), fs in fetches.items():
            fs = sorted(f for f in fs if era_of.get((sp, e, f)) == era)
            k = max(kick[(sp, e)])
            for x, y in zip(fs, fs[1:]):
                gaps.append((y - x) / 60)
                if k - y <= 6 * 3600:
                    last6.append((y - x) / 60)
        out["eras"][era] = {"fetch_interval_min": {"median": q(gaps, .5), "p25": q(gaps, .25), "p75": q(gaps, .75), "n": len(gaps)},
                            "fetch_interval_last_6h_min": {"median": q(last6, .5), "p25": q(last6, .25), "p75": q(last6, .75)},
                            "provider_refresh_lag_min": {"median": q(lag[era], .5), "p95": q(lag[era], .95), "max": q(lag[era], 1.0)}}
    # books
    ev_fetches = {k: len(v) for k, v in fetches.items()}
    for (sp, bk, e), fs in book_f.items():
        b = out["books"].setdefault(f"{sp}|{bk}", {"events": 0, "event_fetches_quoted": 0, "event_fetches_possible": 0})
        b["events"] += 1
        b["event_fetches_quoted"] += len(fs)
        b["event_fetches_possible"] += ev_fetches[(sp, e)]
    marg = collections.defaultdict(list)
    bad = collections.Counter()
    tot = collections.Counter()
    for (sp, e, m, bk, fa), sd in pairs.items():
        tot[(sp, bk)] += 1
        vals = list(sd.values())
        if len(vals) != 2:
            bad[(sp, bk)] += 1
            continue
        (p1, x1), (p2, x2) = vals
        if m == "spreads" and p1 is not None and p2 is not None and abs(p1 + p2) > 1e-9 or \
           m == "totals" and p1 != p2:
            bad[(sp, bk)] += 1
            continue
        imp = lambda p: 100 / (p + 100) if p > 0 else -p / (-p + 100)     # noqa: E731
        marg[(sp, bk)].append(imp(x1) + imp(x2) - 1)
    for key, b in out["books"].items():
        sp, bk = key.split("|")
        b["coverage_of_event_fetches"] = round(b["event_fetches_quoted"] / max(1, b["event_fetches_possible"]), 3)
        b["two_way_pairs"] = tot[(sp, bk)]
        b["unusable_pairs_pct"] = round(100 * bad[(sp, bk)] / max(1, tot[(sp, bk)]), 2)
        b["median_margin_pct"] = round(100 * st.median(marg[(sp, bk)]), 2) if marg[(sp, bk)] else None
    # BetOnline vs LowVig: same operator?
    same = diff = 0
    for (sp, e, m, bk, fa), sd in pairs.items():
        if bk != "betonlineag":
            continue
        o = pairs.get((sp, e, m, "lowvig", fa))
        if o:
            if {k: v[0] for k, v in o.items()} == {k: v[0] for k, v in sd.items()}:
                same += 1
            else:
                diff += 1
    out["operator_identity"] = {"betonline_vs_lowvig_same_fetch": same + diff,
                                "identical_points_pct": round(100 * same / max(1, same + diff), 2),
                                "conclusion": "treated as ONE operator (BetOnline group)"}
    # refresh vs change; resolution of change times
    refresh_no_change = refresh = 0
    windows = collections.defaultdict(list)
    for (e, m, bk), seq in lu_seq.items():
        seq.sort()
        for (f0, lu0, p0, x0), (f1, lu1, p1, x1) in zip(seq, seq[1:]):
            if lu1 != lu0:
                refresh += 1
                refresh_no_change += (p0, x0) == (p1, x1)
            if (p0, x0) != (p1, x1) and bk in ("pinnacle", "draftkings"):
                windows[bk].append((f1 - f0) / 60)
    out["change_resolution"] = {"provider_refreshes": refresh,
                                "refreshes_without_price_change_pct": round(100 * refresh_no_change / max(1, refresh), 1),
                                "interpretation": "market_last_update is the provider's refresh time, not a price-change "
                                                  "time; a change is only known to lie inside its fetch window",
                                "change_window_min": {bk: {"median": q(v, .5), "p75": q(v, .75), "n": len(v)} for bk, v in windows.items()}}
    out["identity"] = {"events": len(fetches),
                       "events_with_team_name_changes": sum(1 for v in names.values() if len(v) > 1),
                       "events_with_kickoff_changes": sum(1 for v in kick.values() if len(v) > 1),
                       "note": "game identity = provider event id; kickoff = latest commence time"}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-db", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rep = audit(sqlite3.connect(a.market_db))
    json.dump(rep, open(a.out, "w"), indent=1)
    print(json.dumps({k: rep[k] for k in ("eras", "operator_identity", "change_resolution", "identity")}, indent=1))


if __name__ == "__main__":
    main()
