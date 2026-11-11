"""
edge_research/prospective.py -- FROZEN prospective experiment "NFL Pinnacle-anchored shop" (PX1). Research only;
not deployed, not scheduled. See edge_research/PROSPECTIVE_PX1.md.

Signal (NFL point spreads only), evaluated at each arm's decision fetch (arm A, primary: latest fetch <= kickoff -
24 h, <= 12 h old; arm B, secondary: latest fetch <= kickoff - 60 min, <= 3 h old):
  * Pinnacle quoted IN that fetch (no BetOnline substitute: the lead is specifically about Pinnacle);
  * among the other books quoted in that fetch (Pinnacle excluded), the side/price with the highest EV against
    Pinnacle's no-vig, push-aware fair mean; SIGNAL when that EV >= 0, otherwise PASS (reason recorded).
  * record: book, side, point, price, EV, Pinnacle fair, retail median, consensus, every fresh book's quote.

  record:  python -m edge_research.prospective record --market-db DB --now ISO --out DIR
           (writes only for games whose decision fetch exists and whose kickoff is still in the future; write-once)
  grade:   python -m edge_research.prospective grade --market-db DB --rankings DIR --results DIR --records DIR
"""
import argparse
import glob
import hashlib
import json
import os
import sqlite3

from edge_research.plan import PLAN
from edge_research.run import SIDES, best, ev_at, result
from intel_v7.history import History, games, view
from need_v6.market import normalizer
from sportsbook_v4.util import from_epoch, to_epoch

PX1 = {"name": "PX1 NFL Pinnacle-anchored shop", "sport": "nfl", "market": "spread",
       # two pre-declared arms: A (primary) at the latest fetch <= kickoff - 24 h (<= 12 h old);
       # B (secondary) at the latest fetch <= kickoff - 60 min (<= 3 h old)
       "arms": {"A": [PLAN["early_offset_s"], PLAN["early_max_age_s"]],
                "B": [PLAN["decision_offset_s"], PLAN["decision_max_age_s"]]},
       "min_ev": 0.0, "pro_book": "pinnacle", "excluded_books": ["pinnacle"],
       "close_max_before_kick_s": PLAN["close_max_before_kick_s"]}
PX1_HASH = hashlib.sha256(json.dumps(PX1, sort_keys=True).encode()).hexdigest()


def decide(h, nz, now=None, arm="A"):
    """PX1 decision for one History and arm. Returns a dict (SIGNAL / PASS), or None when `now` is before the
    arm's decision time."""
    ko = h.kickoff
    off, age = PX1["arms"][arm]
    t = ko - off
    if now is not None and now < t:
        return None
    fd, bd = h.snap_at(t)
    if fd is None or t - fd > age:
        return {"decision": "PASS", "reason": "no decision fetch in the arm's window", "decision_fetch": None}
    v = view(bd)
    rec = {"decision_fetch": from_epoch(fd), "retail_mu": v.get("retail_mu"), "consensus_mu": v.get("consensus_mu"),
           "quotes": {bk: [q["point"], q["price_a"], q["price_b"]] for bk, q in sorted(bd.items())}}
    pin = bd.get(PX1["pro_book"])
    if not pin or pin["mu"] is None:
        return dict(rec, decision="PASS", reason="Pinnacle not quoted in the decision fetch")
    rec["pinnacle_fair_mu"] = pin["mu"]
    cands = [c + (s,) for s in SIDES["spread"]
             for c in [best(nz, pin["mu"], bd, "spread", s, tuple(PX1["excluded_books"]))] if c]
    if not cands:
        return dict(rec, decision="PASS", reason="no other book quoted")
    e, bk, pt, pr, s = max(cands)
    rec.update(best_ev=e, book=bk, side=s, point=pt, price=pr)
    if e < PX1["min_ev"]:
        return dict(rec, decision="PASS", reason="no price at or above Pinnacle fair")
    return dict(rec, decision="SIGNAL")


def record(conn, now, out):
    os.makedirs(out, exist_ok=True)
    nz = normalizer("nfl", "spread")
    n = 0
    for eid, ko, home, away in games(conn, "nfl"):
        if ko <= now:
            continue
        h = History(conn, "nfl", eid, "spread", until=now)
        if h.kickoff is None:
            continue
        for arm in PX1["arms"]:
            path = os.path.join(out, f"px1_nfl_{eid}_{arm}.json")
            if os.path.exists(path):
                continue
            d = decide(h, nz, now, arm)
            if d is None:
                continue
            rec = dict(d, format="px1_v1", px1_sha256=PX1_HASH, arm=arm, event_id=eid, game=f"{away} @ {home}",
                       kickoff=from_epoch(h.kickoff), recorded_at=from_epoch(now))
            rec["record_sha256"] = hashlib.sha256(json.dumps(rec, sort_keys=True, default=float).encode()).hexdigest()
            with open(path, "x") as f:                               # write-once
                json.dump(rec, f, sort_keys=True, indent=1, default=float)
            n += 1
    return n


def grade_one(conn, rec, finals):
    """Grading of one SIGNAL record (after kickoff): CLV vs Pinnacle close (primary), Pinnacle drift toward the bet,
    CLV vs consensus close, price survival in the next fetch, outcome."""
    nz = normalizer("nfl", "spread")
    h = History(conn, "nfl", rec["event_id"], "spread")
    fc, bc = h.snap_at(h.kickoff - 1)
    g = {"event_id": rec["event_id"], "arm": rec.get("arm")}
    if fc is None or h.kickoff - fc > PX1["close_max_before_kick_s"]:
        return dict(g, status="close not observed")
    vc = view(bc)
    side, pt, pr = rec["side"], rec["point"], rec["price"]
    pin_c = bc.get("pinnacle", {}).get("mu")
    if pin_c is not None:
        g["clv_pinnacle_close"] = ev_at(nz, pin_c, side, pt, pr)
        g["pinnacle_drift_toward_bet"] = (pin_c - rec["pinnacle_fair_mu"]) * (1 if side == "home" else -1)
    if vc.get("consensus_mu") is not None:
        g["clv_consensus_close"] = ev_at(nz, vc["consensus_mu"], side, pt, pr)
    nxt = [s for s in h.snaps if s[0] > to_epoch(rec["decision_fetch"])][:1]
    if nxt and rec["book"] in nxt[0][1]:
        q = nxt[0][1][rec["book"]]
        p2, x2 = (q["point"], q["price_a"]) if side == "home" else (-q["point"], q["price_b"])
        g["price_survived_next_fetch"] = ev_at(nz, rec["pinnacle_fair_mu"], side, p2, x2) >= \
            ev_at(nz, rec["pinnacle_fair_mu"], side, pt, pr) - 1e-12
    fin = finals.get(("nfl", rec["event_id"]))
    if fin and None not in fin:
        g["profit"] = result("nfl", "spread", side, pt, pr, fin)
    return dict(g, status="graded")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["record", "grade"])
    for k in ("market-db", "now", "out", "rankings", "results", "records"):
        ap.add_argument("--" + k)
    a = ap.parse_args(argv)
    conn = sqlite3.connect(f"file:{a.market_db}?mode=ro", uri=True)
    if a.cmd == "record":
        print(record(conn, to_epoch(a.now), a.out), "records written")
        return
    from intel_v7.evaluate import load_finals
    recs = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(a.records, "px1_nfl_*_*.json")))]
    ids = {r["event_id"] for r in recs}
    gl = [("nfl", e, k, h, aw) for e, k, h, aw in games(conn, "nfl") if e in ids]
    finals = load_finals(a.rankings, a.results, gl)
    out = [grade_one(conn, r, finals) for r in recs if r["decision"] == "SIGNAL"]
    print(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
