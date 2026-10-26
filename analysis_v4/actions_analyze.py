"""
analysis_v4/actions_analyze.py -- daily analysis inside GitHub Actions.

Rebuilds the append-only quote store from every stored shard (release
assets), then runs the line-shopping closing-EV report for NFL and CFB.
Outputs: line_shop_latest.json and a Markdown summary (shown on the
workflow run page).

  python -m analysis_v4.actions_analyze --shards DIR --out DIR
"""

import argparse
import glob
import gzip
import json
import os
import sys

from analysis_v4 import line_shop
from market_v4 import store

TABLE_COLS = {
    "mkt_fetch": ("fetched_at", "sport", "endpoint", "request_fp", "source", "n_quotes", "n_anomalies"),
    "mkt_quote": ("event_id", "sport", "commence", "home", "away", "book", "market", "side", "point", "price",
                  "market_last_update", "fetched_at", "source", "request_fp"),
    "mkt_anomaly": ("fetched_at", "kind", "event_id", "book", "market", "detail"),
}


def rebuild(shard_paths, db_path=":memory:"):
    conn = store.connect(db_path)
    counts = {t: 0 for t in TABLE_COLS}
    for p in sorted(shard_paths):
        with gzip.open(p, "rt") as f:
            for line in f:
                rec = json.loads(line)
                t = rec.pop("t")
                cols = TABLE_COLS[t]
                sql = f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
                if t != "mkt_anomaly":
                    sql += " ON CONFLICT DO NOTHING"        # a re-uploaded shard cannot double count
                cur = conn.execute(sql, [rec.get(c) for c in cols])
                counts[t] += max(cur.rowcount, 0)
    conn.commit()
    return conn, counts


def markdown(res, counts, n_shards):
    lines = ["## ArbitrEdge V4: line-shopping analysis (real collected odds)", "",
             f"Shards: {n_shards} · fetches: {counts['mkt_fetch']} · quotes: {counts['mkt_quote']}", ""]
    for sport, r in res.items():
        ci = r["closing_ev"]
        lines += [f"### {sport}",
                  f"- opportunities (first sighting, EV ≥ threshold after uncertainty): **{r['opportunities']}**",
                  f"- with an observed close: {r['close_observed']} · close not observed (excluded): {r['close_unobserved']}",
                  f"- closing EV per unit: " + ("n/a" if not ci else
                                                 f"**{ci['mean']:+.2%}** (95% CI {ci['lo']:+.2%} to {ci['hi']:+.2%}, "
                                                 f"{ci['n']} bets / {ci['games']} games)"),
                  f"- verdict: **{r['verdict']}**", ""]
        if r["by_book"]:
            lines += ["| book | n | mean closing EV | 95% CI |", "|---|---|---|---|"]
            for b, c in r["by_book"].items():
                if c:
                    lines.append(f"| {b} | {c['n']} | {c['mean']:+.2%} | {c['lo']:+.2%} … {c['hi']:+.2%} |")
            lines.append("")
    lines.append("_Closing EV = expected value of the price taken, evaluated at the observed closing sharp consensus. "
                 "Realized results are still required before any edge is claimed (rebuild plan §G)._")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    shards = glob.glob(os.path.join(a.shards, "**", "quotes_*.jsonl.gz"), recursive=True)
    conn, counts = rebuild(shards)
    res = {s: line_shop.report(conn, s) for s in ("americanfootball_nfl", "americanfootball_ncaaf")}
    json.dump({"counts": counts, "shards": len(shards), "reports": res},
              open(os.path.join(a.out, "line_shop_latest.json"), "w"), indent=2, default=float)
    md = markdown(res, counts, len(shards))
    with open(os.path.join(a.out, "line_shop_summary.md"), "w") as f:
        f.write(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md + "\n")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
