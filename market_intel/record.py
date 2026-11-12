"""
market_intel/record.py -- PROSPECTIVE write-once records for the original market intelligence (spreads + totals).
DISABLED unless MARKET_INTEL_RECORD=on (owner switch; off by default). Separate release `v4-market-intel`, separate
from CF1/PX1/Need/Official records.

One record per (event, market, cutoff) for cutoffs T-6h and T-1h, written on the first build at or after the cutoff
and before kickoff, evaluated AS OF THE CUTOFF (never later data). PASS / INSUFFICIENT decisions are recorded too.
Each record: cutoff, evaluation time, decision + strength + evidence, the best available price for the NEED side at
the cutoff (all books' quotes kept), data freshness, record checksum. Grading (after the game) adds the result,
ROI at the recorded price and closing value -- in a separate graded file, never by editing the record.
"""
import hashlib
import json
import os

from market_intel import LABEL
from market_intel.compat import build, iso

CUTOFFS = {"T-6h": 6 * 3600, "T-1h": 3600}
FORMAT = "market_intel_record_v1"


def enabled():
    return os.environ.get("MARKET_INTEL_RECORD") == "on"


def _quotes(conn, gid, t, market):
    rows = conn.execute("SELECT book, MAX(observed_at), spread_home, spread_price_home, spread_price_away, total_points, "
                        "total_price_over, total_price_under FROM market_snapshots WHERE canonical_game_id=? AND "
                        "observed_at<=? GROUP BY book", (gid, iso(t))).fetchall()
    if market == "spread":
        return {b: {"observed_at": o, "home_point": sh, "home_price": ph, "away_price": pa}
                for b, o, sh, ph, pa, *_ in rows if sh is not None}
    return {b: {"observed_at": o, "total": tp, "over_price": po, "under_price": pu}
            for b, o, _, _, _, tp, po, pu in rows if tp is not None}


def _best(quotes, market, side):
    best = None
    for b, q in quotes.items():
        if market == "spread":
            pt = q["home_point"] if side == "home" else -q["home_point"]
            pr = q["home_price"] if side == "home" else q["away_price"]
        else:
            pt, pr = q["total"], (q["over_price"] if side == "over" else q["under_price"])
        if pr is None:
            continue
        # better number first (more points for the bettor), then better price
        key = ((pt if market == "spread" else (-pt if side == "over" else pt)), pr)
        if best is None or key > best[0]:
            best = (key, b, pt, pr)
    return None if best is None else {"book": best[1], "point": best[2], "price": best[3]}


def record(store_conn, finals, polls, now, out, existing=()):
    """Writes new records for every cutoff that has passed for a not-yet-started game. Returns written names."""
    from market_intel.engine import spread_card, totals_card
    existing = set(existing)
    conn, ids = build(store_conn, finals, polls)
    written, cache = [], {}
    for eid, (gid, sp, home, away, ko) in ids.items():
        if ko <= now:
            continue
        for label, off in CUTOFFS.items():
            t = ko - off
            if now < t:
                continue
            for market in ("spread", "total"):
                name = f"mi_{sp}_{eid}_{market}_{label.replace('-', '')}.json"
                path = os.path.join(out, name)
                if name in existing or os.path.exists(path):
                    continue
                card = spread_card(conn, gid, iso(t)) if market == "spread" else totals_card(conn, gid, iso(t), cache)
                q = _quotes(conn, gid, t, market)
                rec = {"format": FORMAT, "label": LABEL, "sport": sp, "event_id": eid, "game": f"{away} @ {home}",
                       "market": market, "cutoff": label, "evaluated_as_of": iso(t), "recorded_at": iso(now),
                       "kickoff": iso(ko), "decision": card.get("decision"), "need": card.get("need"),
                       "strength": card.get("strength"), "evidence": card, "quotes_at_cutoff": q,
                       "price_for_need": _best(q, market, card["need"]) if card.get("need") else None}
                rec["record_sha256"] = hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()
                os.makedirs(out, exist_ok=True)
                with open(path, "x") as f:                               # write-once
                    json.dump(rec, f, sort_keys=True, indent=1, default=str)
                written.append(name)
    return written
