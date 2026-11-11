"""
lab_v4/cfb_follow.py -- FROZEN prospective tracker "CFB line follow" (CF1). Research only; not deployed.

RULE (frozen; any change = new hash = new experiment):
  * CFB, point spreads and totals, every game in the collected quote store.
  * Opening reference = the FIRST collected pre-kickoff fetch of that game-market with a consensus fair line.
  * Decision fetch = the latest collected fetch <= kickoff - 60 min, no more than 3 h old.
  * Move = consensus fair mean at decision - consensus fair mean at the opening reference (all-book median of
    no-vig, push-aware fair lines; spreads = expected home margin, totals = expected total).
  * SIGNAL when |move| >= 1.0 point: bet the side the line moved TOWARD (spread: home if the expected home margin
    rose, else away; total: over if the expected total rose, else under). Otherwise PASS.
  * Price = the best price for that side among the books quoted IN the decision fetch (highest EV at the decision
    consensus). One bet per game-market.
  * Graded on the final score at that price (push = 0), with closing value vs the consensus close.

Development evidence (2026 through 2026-11-10, EXPLORATORY, already-studied season):
  321-271-4 (54.2%), ROI +5.1% (95% CI -3% to +13%), closing value -1.75%; before 3 Oct 148-122, after 173-149.

  record:  python -m lab_v4.cfb_follow record --market-db DB --now ISO --out DIR
  grade:   python -m lab_v4.cfb_follow grade  --market-db DB --rankings DIR --results DIR --records DIR
  backtest: python -m lab_v4.cfb_follow backtest --market-db DB --rankings DIR --results DIR [--before ISO]
"""
import argparse
import glob
import hashlib
import json
import os
import random
import sqlite3
import statistics as st

from intel_v7.history import History, games, view
from need_v6.market import normalizer
from sportsbook_v4.market import profit_on_win
from sportsbook_v4.util import from_epoch, to_epoch

RULE = {"name": "CF1 CFB line follow", "sport": "cfb", "markets": ["spread", "total"], "move_pts": 1.0,
        "decision_offset_s": 3600, "decision_max_age_s": 3 * 3600, "close_max_before_kick_s": 5400,
        "side": "toward the move", "price": "best fresh price in the decision fetch"}
RULE_HASH = hashlib.sha256(json.dumps(RULE, sort_keys=True).encode()).hexdigest()
SIDES = {"spread": ("home", "away"), "total": ("over", "under")}
SIGN = {"home": 1, "over": 1, "away": -1, "under": -1}
# Judgement rules, fixed BEFORE any prospective record exists (evaluated once, after the 2026-27 bowl season):
JUDGEMENT = {"min_signals": 150,
             "abandon": "ROI <= 0% or closing value <= -2.0%",
             "continue_2027": "ROI > 0% and closing value > -2.0% (report only; claim nothing)",
             "promising": "one-sided p < 0.05 on ROI (game-clustered bootstrap) with >= 150 signals",
             "validated": "requires a second independent season meeting 'promising'"}


def _quotes(books, m, side):
    a = SIDES[m][0]
    for bk, q in books.items():
        if q["mu"] is None:
            continue
        yield (bk, q["point"], q["price_a"]) if side == a else \
            (bk, (-q["point"] if m == "spread" else q["point"]), q["price_b"])


def ev(nz, mu, side, pt, pr):
    pw, pp = nz.prob_at(mu, side, pt)
    return pw * profit_on_win(1.0, pr) - (1 - pw - pp)


def decide(h, now=None):
    """CF1 decision for one History (point in time: pass History(..., until=now) for live use)."""
    m = h.market
    ko = h.kickoff
    t = ko - RULE["decision_offset_s"]
    if now is not None and now < t:
        return None
    fd, bd = h.snap_at(t)
    if fd is None or t - fd > RULE["decision_max_age_s"]:
        return {"decision": "PASS", "reason": "no decision fetch"}
    first = next(((f, b) for f, b in h.snaps if view(b).get("consensus_mu") is not None and f <= fd), None)
    vd = view(bd)
    if first is None or vd.get("consensus_mu") is None:
        return {"decision": "PASS", "reason": "no consensus"}
    f0, b0 = first
    mu0, mud = view(b0)["consensus_mu"], vd["consensus_mu"]
    move = mud - mu0
    rec = {"open_fetch": from_epoch(f0), "decision_fetch": from_epoch(fd), "mu_open": mu0, "mu_decision": mud,
           "move": move, "quotes": {bk: [q["point"], q["price_a"], q["price_b"]] for bk, q in sorted(bd.items())}}
    if abs(move) < RULE["move_pts"]:
        return dict(rec, decision="PASS", reason=f"move {move:+.2f} < {RULE['move_pts']}")
    side = SIDES[m][0] if move > 0 else SIDES[m][1]
    nz = normalizer("cfb", m)
    best = max(((ev(nz, mud, side, pt, pr), bk, pt, pr) for bk, pt, pr in _quotes(bd, m, side)), default=None)
    if best is None:
        return dict(rec, decision="PASS", reason="no price for the side")
    _, bk, pt, pr = best
    return dict(rec, decision="SIGNAL", side=side, book=bk, point=pt, price=pr)


def grade(conn, rec, final):
    h = History(conn, "cfb", rec["event_id"], rec["market"])
    out = {"event_id": rec["event_id"], "market": rec["market"]}
    fc, bc = h.snap_at(h.kickoff - 1)
    if fc is not None and h.kickoff - fc <= RULE["close_max_before_kick_s"] and view(bc).get("consensus_mu") is not None:
        out["clv"] = ev(normalizer("cfb", rec["market"]), view(bc)["consensus_mu"], rec["side"], rec["point"], rec["price"])
    if final and None not in final:
        hs, as_ = final
        v = ((hs - as_) * SIGN[rec["side"]] + rec["point"]) if rec["market"] == "spread" else \
            ((hs + as_ - rec["point"]) * SIGN[rec["side"]])
        out["result"] = "W" if v > 0 else "P" if v == 0 else "L"
        out["units"] = profit_on_win(1.0, rec["price"]) if v > 0 else (0.0 if v == 0 else -1.0)
    return out


def summarize(graded, B=2000, seed=1):
    g = [x for x in graded if x.get("units") is not None]
    if not g:
        return {"n": 0}
    u = [x["units"] for x in g]
    rng = random.Random(seed)
    bs = sorted(st.mean(rng.choice(u) for _ in u) for _ in range(B))
    clv = [x["clv"] for x in graded if x.get("clv") is not None]
    w = sum(x["result"] == "W" for x in g)
    l = sum(x["result"] == "L" for x in g)
    return {"n": len(g), "record": f"{w}-{l}-{len(g) - w - l}", "win_pct": 100 * w / max(1, w + l),
            "roi_pct": 100 * st.mean(u), "roi_ci": [100 * bs[int(.025 * B)], 100 * bs[int(.975 * B) - 1]],
            "p_roi_le0": (sum(x <= 0 for x in bs) + 1) / (B + 1), "clv_pct": 100 * st.mean(clv) if clv else None}


def record(conn, now, out):
    os.makedirs(out, exist_ok=True)
    n = 0
    for eid, ko, home, away in games(conn, "cfb"):
        if ko <= now:
            continue
        for m in RULE["markets"]:
            path = os.path.join(out, f"cf1_{eid}_{m}.json")
            if os.path.exists(path):
                continue
            h = History(conn, "cfb", eid, m, until=now)
            if h.kickoff is None or h.kickoff <= now:
                continue
            d = decide(h, now)
            if d is None:
                continue
            rec = dict(d, format="cf1_v1", rule_sha256=RULE_HASH, event_id=eid, market=m, game=f"{away} @ {home}",
                       kickoff=from_epoch(h.kickoff), recorded_at=from_epoch(now))
            rec["record_sha256"] = hashlib.sha256(json.dumps(rec, sort_keys=True, default=float).encode()).hexdigest()
            with open(path, "x") as f:                                   # write-once
                json.dump(rec, f, sort_keys=True, indent=1, default=float)
            n += 1
    return n


def _finals(conn, rankings, results):
    from intel_v7.evaluate import load_finals
    gl = [("cfb", e, k, h, a) for e, k, h, a in games(conn, "cfb")]
    return load_finals(rankings, results, gl)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["record", "grade", "backtest"])
    for k in ("market-db", "now", "out", "rankings", "results", "records", "before"):
        ap.add_argument("--" + k)
    a = ap.parse_args(argv)
    conn = sqlite3.connect(f"file:{a.market_db}?mode=ro", uri=True)
    if a.cmd == "record":
        print(record(conn, to_epoch(a.now), a.out), "records written")
        return
    finals = _finals(conn, a.rankings, a.results)
    if a.cmd == "grade":
        recs = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(a.records, "cf1_*.json")))]
        sig = [r for r in recs if r["decision"] == "SIGNAL" and to_epoch(r["recorded_at"]) < to_epoch(r["kickoff"])]
        graded = [grade(conn, r, finals.get(("cfb", r["event_id"]))) for r in sig]
        print(json.dumps({"rule_sha256": RULE_HASH, "records": len(recs), "signals": len(sig),
                          "summary": summarize(graded), "judgement_rules": JUDGEMENT}, indent=1, default=float))
        return
    graded = []
    before = to_epoch(a.before) if a.before else None
    for eid, ko, home, away in games(conn, "cfb"):
        if (before and ko >= before) or ("cfb", eid) not in finals:
            continue
        for m in RULE["markets"]:
            h = History(conn, "cfb", eid, m)
            if not h.snaps:
                continue
            d = decide(h)
            if d and d["decision"] == "SIGNAL":
                graded.append(grade(conn, dict(d, event_id=eid, market=m), finals[("cfb", eid)]))
    print(json.dumps({"rule_sha256": RULE_HASH, "backtest_EXPLORATORY": summarize(graded)}, indent=1, default=float))


if __name__ == "__main__":
    main()
