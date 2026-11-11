"""
lab_v4/audit.py -- Phase 1 audit diagnostics of the PRODUCTION configuration (A, seed 0) that the run summaries do not
hold: wager timing vs kickoff, age of the collected quote each wager was priced from (collection cadence), stake
limits hit, worst-case exposure per game-market vs the v6 game limits, and where sharp customers' edge comes from.

  python -m lab_v4.audit --market-db market.db --rankings DIR --results DIR --out audit.json
"""
import argparse
import bisect
import collections
import json
import sqlite3

from collect_v4 import backfill as B
from intel_v7.evaluate import load_finals
from lab_v4.config import CONFIGS, RISK
from lab_v4.experiment import game_list
from lab_v4.season import run_season
from need_v6.book import Exposure
from sportsbook_v4.params import BOOK
from tools_v4.backfill_replay import load_polls


def q(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 2) if xs else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-db", required=True)
    ap.add_argument("--rankings", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    live = sqlite3.connect(a.market_db)
    polls = B.poll_snapshots(load_polls(a.rankings))
    gl = game_list(live, polls)
    finals = load_finals(a.rankings, a.results, gl)
    weeks = run_season(live, polls, finals, CONFIGS["A"], 0)
    fetch = collections.defaultdict(list)
    for e, fa in live.execute("SELECT DISTINCT event_id, fetched_at FROM mkt_quote WHERE market IN ('spreads','totals','h2h')"):
        fetch[e].append(fa)
    for v in fetch.values():
        v.sort()
    kick = {(s, e): k for s, e, k, _, _ in gl}
    out = {"timing": {}, "quote_age_min": {}, "stakes": {}, "exposure": {}, "sharps": {}}
    buckets = [(168, ">72h"), (72, "24-72h"), (24, "6-24h"), (6, "1-6h"), (1, "<1h")]
    for sport in ("nfl", "cfb"):
        ws = [w for wk in weeks if wk["sport"] == sport for w in wk["wagers"]]
        hrs = [(kick[(sport, w["canonical_game_id"])] - w["t"]) / 3600 for w in ws]
        cnt = collections.Counter()
        hcnt = collections.Counter()
        for w, h in zip(ws, hrs):
            lab = next((lb for lim, lb in reversed(buckets) if h < lim), ">72h")
            cnt[lab] += 1
            hcnt[lab] += w["stake"]
        out["timing"][sport] = {"wagers_by_hours_to_kickoff": dict(cnt),
                                "handle_share_by_hours_to_kickoff": {k: round(v / sum(hcnt.values()), 3) for k, v in hcnt.items()},
                                "median_hours_before_kickoff": q(hrs, .5)}
        ages, ages_sharp = [], []
        for w in ws:
            f = fetch.get(w["canonical_game_id"], [])
            i = bisect.bisect_right(f, w["t"] - BOOK["offer_lag_seconds"])
            if i:
                age = (w["t"] - f[i - 1]) / 60
                (ages_sharp if w["respected"] else ages).append(age)
        out["quote_age_min"][sport] = {"recreational_median": q(ages, .5), "recreational_p90": q(ages, .9),
                                       "sharp_median": q(ages_sharp, .5), "sharp_p90": q(ages_sharp, .9),
                                       "note": "minutes between the wager and the collected quote it was priced from"}
        rec_cap = BOOK["max_wager_default"]
        sh_cap = BOOK["max_wager_by_archetype"]["SHARP"]
        out["stakes"][sport] = {
            "recreational_at_cap_pct": round(100 * sum(1 for w in ws if not w["respected"] and w["stake"] >= rec_cap) / max(1, sum(1 for w in ws if not w["respected"])), 2),
            "sharp_at_cap_pct": round(100 * sum(1 for w in ws if w["respected"] and w["stake"] >= sh_cap) / max(1, sum(1 for w in ws if w["respected"])), 2),
            "stake_p50": q([w["stake"] for w in ws], .5), "stake_p99": q([w["stake"] for w in ws], .99),
            "max_stake": max((w["stake"] for w in ws), default=None)}
        worst = []
        for wk in weeks:
            if wk["sport"] != sport:
                continue
            ex = {}
            for w in wk["wagers"]:
                if w["market"] in ("spread", "total"):
                    ex.setdefault((w["canonical_game_id"], w["market"]), Exposure(w["market"])).add(
                        w["side"], w["accepted_line"], w["accepted_price"], w["stake"])
            worst += [-e.worst() for e in ex.values()]
        lim = RISK["game_limit"][sport]
        out["exposure"][sport] = {"game_markets": len(worst), "worst_case_median": q(worst, .5), "worst_case_p90": q(worst, .9),
                                  "worst_case_max": q(worst, 1.0), "v6_game_limit": lim,
                                  "over_limit_pct": round(100 * sum(x > lim for x in worst) / max(1, len(worst)), 1)}
        sh = [w for w in ws if w["respected"]]
        out["sharps"][sport] = {"wagers": len(sh), "share_of_handle": round(sum(w["stake"] for w in sh) / max(1, sum(w["stake"] for w in ws)), 4),
                                "settled_pnl": round(sum(w["customer_pnl"] or 0 for w in sh), 2)}
    json.dump(out, open(a.out, "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
