"""
market_intel/grade.py -- grade frozen market-intelligence records after the game, WITHOUT touching them: each
graded result is a separate new file `graded_<record name>` (write-once). Result and ROI at the recorded available
price for the NEED side; closing value = EV of that price at the consensus closing fair line (last pre-kickoff
quote of every book, no-vig, push-aware; cf1.normalize). PASS records are graded as PASS (no bet).
"""
import hashlib
import json
import os
import statistics as st

from cf1 import normalize as NZ
from sportsbook_v4.market import profit_on_win

_N = {}


def _nz(sport, market):
    if not _N:
        nm, nt = NZ.nfl()
        cm, ct = NZ.cfb_plain()
        _N.update({("nfl", "spread"): nm, ("nfl", "total"): nt, ("cfb", "spread"): cm, ("cfb", "total"): ct})
    return _N[(sport, market)]


def closing_fair(store, sport, event_id, market, kickoff_epoch):
    mk = {"spread": "spreads", "total": "totals"}[market]
    a, b = ("home", "away") if market == "spread" else ("over", "under")
    rows = store.execute("SELECT book, side, point, price, fetched_at FROM mkt_quote WHERE event_id=? AND market=? "
                         "AND fetched_at<? ORDER BY fetched_at", (event_id, mk, kickoff_epoch)).fetchall()
    last = {}
    for bk, sd, pt, pr, fa in rows:
        last.setdefault(bk, {})[sd] = (pt, pr, fa)
    mus = []
    for bk, q in last.items():
        if a in q and b in q and q[a][2] == q[b][2] and kickoff_epoch - q[a][2] <= 5400:
            z = _nz(sport, market).quote(a, q[a][0], q[a][1], b, q[b][0], q[b][1])
            if z:
                mus.append(z["mu"])
    return st.median(mus) if mus else None


def grade(rec, final, store, kickoff_epoch):
    g = {"record": rec["record_sha256"], "decision": rec["decision"], "graded_format": "market_intel_graded_v1"}
    if not rec.get("need") or not rec.get("price_for_need"):
        return dict(g, status="PASS (no bet)")
    if not final or None in final:
        return dict(g, status="NO FINAL SCORE")
    side, p = rec["need"], rec["price_for_need"]
    hs, as_ = final
    v = ((hs - as_) * (1 if side == "home" else -1) + p["point"]) if rec["market"] == "spread" else \
        ((hs + as_ - p["point"]) * (1 if side == "over" else -1))
    units = profit_on_win(1.0, p["price"]) if v > 0 else (0.0 if v == 0 else -1.0)
    mu = closing_fair(store, rec["sport"], rec["event_id"], rec["market"], kickoff_epoch)
    clv = None
    if mu is not None:
        pw, pp = _nz(rec["sport"], rec["market"]).prob_at(mu, side, p["point"])
        clv = pw * profit_on_win(1.0, p["price"]) - (1 - pw - pp)
    return dict(g, status="GRADED", final=[hs, as_], result="WIN" if v > 0 else "PUSH" if v == 0 else "LOSS",
                units=units, profit_at_100=round(100 * units, 2), clv=clv, agreement=(rec.get("agreement") or {}).get("category"))


def grade_dir(records_dir, finals, store, out, existing=()):
    from sportsbook_v4.util import to_epoch
    existing = set(existing)
    written = []
    for name in sorted(os.listdir(records_dir)):
        if not name.startswith("mi_") or ("graded_" + name) in existing:
            continue
        rec = json.load(open(os.path.join(records_dir, name)))
        k = (rec["sport"], rec["event_id"])
        if k not in finals and rec.get("need"):
            continue                                   # wait for the final score
        g = grade(rec, finals.get(k), store, to_epoch(rec["kickoff"]))
        g["graded_sha256"] = hashlib.sha256(json.dumps(g, sort_keys=True, default=str).encode()).hexdigest()
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "graded_" + name), "x") as f:
            json.dump(g, f, sort_keys=True, indent=1, default=str)
        written.append("graded_" + name)
    return written


def main(argv=None):
    """python -m market_intel.grade --records DIR --shards DIR --results DIR --rankings DIR --out DIR --existing FILE"""
    import argparse
    import glob as _g
    ap = argparse.ArgumentParser()
    for k in ("records", "shards", "results", "rankings", "out", "existing"):
        ap.add_argument("--" + k)
    a = ap.parse_args(argv)
    from data_v4 import rankings as RK
    from data_v4 import results as RS
    from web_v4.live_site import load_rankings, rebuild_store
    store, _, _ = rebuild_store(a.shards)
    recs = [json.load(open(p)) for p in _g.glob(os.path.join(a.records, "mi_*.json"))]
    snaps = load_rankings(a.rankings)
    latest = max((x for x in snaps if x.get("teams")), key=lambda x: x["fetched_at"], default=None)
    games = list({(r["sport"], r["event_id"]): {"sport": r["sport"], "event_id": r["event_id"],
                                                 "home": r["game"].split(" @ ")[1], "away": r["game"].split(" @ ")[0],
                                                 "kickoff": r["kickoff"][:19] + "Z"} for r in recs}.values())
    m = RS.match(games, RS.load(a.results), RK.TeamMatcher(latest["teams"]) if latest else None)
    finals = {k: (v["home_score"], v["away_score"]) for k, v in m.items()}
    ex = [x.strip() for x in open(a.existing)] if a.existing and os.path.exists(a.existing) else []
    print(len(grade_dir(a.records, finals, store, a.out, ex)), "graded files written")


if __name__ == "__main__":
    main()
