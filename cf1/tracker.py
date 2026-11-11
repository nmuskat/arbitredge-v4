"""
cf1/tracker.py -- CF1 "CFB line follow": frozen prospective research records (owner-approved 2026-11-11).

RULE (identical to research lab_v4/cfb_follow.py; same RULE_HASH):
  * CFB spreads and totals, every collected game.
  * Opening reference = first collected pre-kickoff fetch with a consensus fair line; decision fetch = latest fetch
    <= kickoff - 60 min (<= 3 h old). Move = consensus fair mean (decision) - consensus fair mean (opening).
  * SIGNAL when |move| >= 1.0 point: the side the line moved toward, at the best price in the decision fetch.
Fair means: no-vig, push-aware (cf1.normalize, plain CFB distribution). Consensus = median over operators
(BetOnline + LowVig counted once). Point in time: only fetches <= the build time; never a quote after kickoff.
Research records only; no bet is placed, recommended or shown.

  python -m cf1.tracker record --shards DIR --now ISO --out DIR [--existing names.txt]
"""
import argparse
import bisect
import hashlib
import json
import os
import statistics as st

from cf1 import normalize as NZ
from sportsbook_v4.market import profit_on_win

RULE = {"name": "CF1 CFB line follow", "sport": "cfb", "markets": ["spread", "total"], "move_pts": 1.0,
        "decision_offset_s": 3600, "decision_max_age_s": 3 * 3600, "close_max_before_kick_s": 5400,
        "side": "toward the move", "price": "best fresh price in the decision fetch"}
RULE_HASH = hashlib.sha256(json.dumps(RULE, sort_keys=True).encode()).hexdigest()
SIDES = {"spread": ("home", "away"), "total": ("over", "under")}
MK = {"spread": "spreads", "total": "totals"}
OPERATORS = {"betonlineag": "betonline_group", "lowvig": "betonline_group"}
_NZ = {}


def normalizer(market):
    if not _NZ:
        m, t = NZ.cfb_plain()
        _NZ.update(spread=m, total=t)
    return _NZ[market]


def _iso(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(s):
    from datetime import datetime, timezone
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


class History:
    """Per-fetch fair means of every fresh two-way quote of one game-market, fetched before kickoff and <= until."""

    def __init__(self, conn, event_id, market, until):
        a, b = SIDES[market]
        nz = normalizer(market)
        raw = {}
        self.kickoff = None
        q = ("SELECT fetched_at, book, side, point, price, commence FROM mkt_quote WHERE event_id=? AND market=? "
             "AND fetched_at<=? ORDER BY fetched_at")
        for fa, bk, sd, pt, pr, cm in conn.execute(q, (event_id, MK[market], until)):
            if fa >= cm:
                continue
            self.kickoff = cm
            raw.setdefault(fa, {}).setdefault(bk, {})[sd] = (pt, pr)
        self.snaps = []
        for fa in sorted(raw):
            books = {}
            for bk, sd in raw[fa].items():
                if a in sd and b in sd:
                    (pa, xa), (pb, xb) = sd[a], sd[b]
                    z = nz.quote(a, pa, xa, b, pb, xb)
                    books[bk] = {"point": pa, "price_a": xa, "price_b": xb, "mu": None if z is None else z["mu"]}
            if books:
                self.snaps.append((fa, books))
        self.times = [t for t, _ in self.snaps]

    def snap_at(self, t):
        i = bisect.bisect_right(self.times, t)
        return self.snaps[i - 1] if i else (None, {})


def consensus(books):
    ops = {}
    for bk, q in books.items():
        if q["mu"] is not None:
            ops.setdefault(OPERATORS.get(bk, bk), []).append(q["mu"])
    vals = [st.median(v) for v in ops.values()]
    return st.median(vals) if vals else None


def ev(nz, mu, side, pt, pr):
    pw, pp = nz.prob_at(mu, side, pt)
    return pw * profit_on_win(1.0, pr) - (1 - pw - pp)


def decide(h, market, now):
    t = h.kickoff - RULE["decision_offset_s"]
    if now < t:
        return None
    fd, bd = h.snap_at(t)
    if fd is None or t - fd > RULE["decision_max_age_s"]:
        return {"decision": "PASS", "reason": "no decision fetch"}
    first = next(((f, b) for f, b in h.snaps if f <= fd and consensus(b) is not None), None)
    mud = consensus(bd)
    if first is None or mud is None:
        return {"decision": "PASS", "reason": "no consensus"}
    f0, b0 = first
    mu0 = consensus(b0)
    move = mud - mu0
    rec = {"open_fetch": _iso(f0), "decision_fetch": _iso(fd), "mu_open": mu0, "mu_decision": mud, "move": move,
           "quotes": {bk: [q["point"], q["price_a"], q["price_b"]] for bk, q in sorted(bd.items())}}
    if abs(move) < RULE["move_pts"]:
        return dict(rec, decision="PASS", reason=f"move {move:+.2f} < {RULE['move_pts']}")
    a, b = SIDES[market]
    side = a if move > 0 else b
    nz = normalizer(market)
    cands = []
    for bk, q in bd.items():
        if q["mu"] is None:
            continue
        pt, pr = (q["point"], q["price_a"]) if side == a else ((-q["point"] if market == "spread" else q["point"]), q["price_b"])
        cands.append((ev(nz, mud, side, pt, pr), bk, pt, pr))
    if not cands:
        return dict(rec, decision="PASS", reason="no price for the side")
    _, bk, pt, pr = max(cands)
    return dict(rec, decision="SIGNAL", side=side, book=bk, point=pt, price=pr)


def record(conn, now, out, existing=()):
    os.makedirs(out, exist_ok=True)
    existing = set(existing)
    rows = conn.execute("SELECT event_id, home, away, MAX(fetched_at) FROM mkt_quote WHERE sport='americanfootball_ncaaf' "
                        "AND market IN ('spreads','totals') AND fetched_at<=? GROUP BY event_id", (now,)).fetchall()
    written = []
    for eid, home, away, _ in rows:
        for m in RULE["markets"]:
            name = f"cf1_{eid}_{m}.json"
            path = os.path.join(out, name)
            if name in existing or os.path.exists(path):
                continue
            h = History(conn, eid, m, until=now)
            if h.kickoff is None or h.kickoff <= now or not h.snaps:
                continue
            d = decide(h, m, now)
            if d is None:
                continue
            rec = dict(d, format="cf1_v1", rule_sha256=RULE_HASH, event_id=eid, market=m, game=f"{away} @ {home}",
                       kickoff=_iso(h.kickoff), recorded_at=_iso(now))
            rec["record_sha256"] = hashlib.sha256(json.dumps(rec, sort_keys=True, default=float).encode()).hexdigest()
            with open(path, "x") as f:                            # write-once
                json.dump(rec, f, sort_keys=True, indent=1, default=float)
            written.append(name)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["record"])
    ap.add_argument("--shards", required=True)
    ap.add_argument("--now", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--existing")
    a = ap.parse_args(argv)
    from web_v4.live_site import rebuild_store
    conn, _, n = rebuild_store(a.shards)
    existing = [x.strip() for x in open(a.existing)] if a.existing and os.path.exists(a.existing) else []
    w = record(conn, _epoch(a.now), a.out, existing)
    print(f"CF1: {n} shards, {len(w)} new records ({sum(1 for x in w if 'SIGNAL' in open(os.path.join(a.out, x)).read())} signals)")


if __name__ == "__main__":
    main()
