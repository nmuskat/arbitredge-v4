"""
splits_v4/plays.py -- the PLAY RULE (owner's idea, 2026-11-12; thresholds fixed before any result was seen):

"If the public betting splits show heavy public bets on one side and Pinnacle and Circa are not moving toward it
(not respecting that action), that is a play" -- on the OTHER side.

Per game, per market (spread, total), from one splits snapshot taken ~1 h before the slate:
 1. PUBLIC SIDE: average % of BETS across the independent real-money sources >= 65%, with at least 2 sources each
    >= 60% on that side, and at least 2 sources available. Independent sources (each counted ONCE):
      DraftKings (WagerTalk republishes DraftKings: used only if DraftKings itself is missing),
      the ScoresAndOdds / SportsBettingDime feed (ScoresAndOdds; SportsBettingDime if it is missing),
      BetMGM (bets % only). Covers = free contest picks: shown, never counted.
 2. PINNACLE: its own price move since we first saw the game, up to the snapshot (analysis_v4.book_moves point
    equivalents). Moved >= 0.25 TOWARD the public side (public side made more expensive) = RESPECTED -> no play.
    Moved >= 0.25 against = STRONG; otherwise HOLD.
 3. CIRCA (from WagerTalk at the snapshot): its number moved >= 0.5 toward the public side vs the market opener =
    RESPECTED -> no play. Not available = noted, does not block.
 4. PLAY = the side opposite the public, at the best real price in our odds feed at the snapshot time.
CFB plays: AP Top-25 games (status unknown also kept, as in collection). Graded W/L/P at the recorded number,
units at the recorded price (1 unit risked). Records are sealed once; results are added at display time.
EXPERIMENTAL: not a proven strategy.
"""
import time

from analysis_v4 import book_moves as BM
from market_v4 import asof

RULE = "splits_fade.v1 (2026-11-12): avg bets >= 65% (>= 2 sources >= 60%); Pinnacle/Circa not moving toward it"
PUBLIC_AVG, PUBLIC_EACH, MIN_SOURCES = 65.0, 60.0, 2
PIN_MOVE, CIRCA_MOVE = 0.25, 0.5
SOURCES = (("DraftKings", ("draftkings", "wagertalk")), ("ScoresAndOdds/SBD", ("scoresandodds", "sportsbettingdime")),
           ("BetMGM", ("betmgm",)))
MK = {"spread": ("spreads", ("away", "home")), "total": ("totals", ("over", "under"))}
OPP = {"away": "home", "home": "away", "over": "under", "under": "over"}


def _bets(game, market):
    """[(label, site, {side: bets%})] -- one per independent source."""
    out = []
    for label, sites in SOURCES:
        for site in sites:
            m = ((game.get(site) or {}).get(market) or {})
            sides = MK[market][1]
            if all((m.get(s) or {}).get("bets") is not None for s in sides):
                out.append((label, site, {s: float(m[s]["bets"]) for s in sides}))
                break
    return out


def public_side(game, market):
    src = _bets(game, market)
    if len(src) < MIN_SOURCES:
        return None, src, f"only {len(src)} real-money source(s)"
    for s in MK[market][1]:
        avg = sum(x[2][s] for x in src) / len(src)
        if avg >= PUBLIC_AVG and sum(x[2][s] >= PUBLIC_EACH for x in src) >= 2:
            return s, src, f"{avg:.0f}% of bets on {s} (avg of {len(src)})"
    return None, src, "no side with heavy public bets"


def pinnacle(conn, event_id, market, until, side):
    mk = MK[market][0]
    ser = BM.series(conn, event_id, "pinnacle", mk, until)
    if not ser:
        return "NO DATA", None, None
    move = ser[-1][4] - ser[0][4]                       # + = home/over more expensive
    toward = move if side in ("home", "over") else -move
    st = "RESPECTED" if toward >= PIN_MOVE else "STRONG" if toward <= -PIN_MOVE else "HOLD"
    return st, round(toward, 2), {"first": ser[0][1], "now": ser[-1][1], "first_prices": ser[0][2:4], "now_prices": ser[-1][2:4]}


def circa(game, market, side):
    w = game.get("wagertalk") or {}
    c, o = w.get("circa") or {}, w.get("open") or {}
    if market == "spread":
        k = "spread_" + side
        if c.get(k) is None or o.get(k) is None:
            return "N/A", None
        toward = o[k] - c[k]                            # public side's number got worse (e.g. -2.5 -> -3, +3 -> +2.5)
    else:
        if c.get("total") is None or o.get("total") is None:
            return "N/A", None
        toward = (c["total"] - o["total"]) * (1 if side == "over" else -1)
    return ("RESPECTED" if toward >= CIRCA_MOVE else "HOLD"), {"open": o.get(k if market == "spread" else "total"),
                                                             "circa": c.get(k if market == "spread" else "total")}


def best_price(conn, event_id, market, side, at):
    st = asof.market_state(conn, event_id, MK[market][0], at)
    best = None
    for book, q in (st.get("books") or {}).items():
        if not q.get("fresh") or not q.get(side):
            continue
        price, line = q[side]
        key = ((line if market == "spread" else (-line if side == "over" else line)), price)
        if best is None or key > best[0]:
            best = (key, book, line, price)
    return None if best is None else {"book": best[1], "line": best[2], "price": int(best[3])}


def decide(snapshot, conn, top25=None):
    """Sealed play records (one per game x market evaluated) for one snapshot. top25: {event_id: True/False/None}."""
    at = snapshot["taken_at"]
    out = []
    for e in snapshot["events"]:
        g = snapshot["games"].get(e["event_id"]) or {}
        for market in MK:
            pub, src, why = public_side(g, market)
            rec = {"rule": RULE, "event_id": e["event_id"], "sport": e["sport"], "game": f"{e['away']} @ {e['home']}",
                   "kickoff": e["kickoff"], "market": market, "taken_at": at, "timing": snapshot.get("timing"),
                   "sources": [{"label": lb, "site": s, "bets": v} for lb, s, v in src],
                   "covers": ((g.get("covers") or {}).get(market) or None), "public_side": pub, "why_public": why,
                   "play": None, "status": "NO PICK"}
            if e["sport"] == "cfb" and top25 is not None and top25.get(e["event_id"]) is False:
                rec["status"], rec["reason"] = "NOT COUNTED", "not an AP Top-25 game"
            elif pub is None:
                rec["reason"] = why
            else:
                pst, ptoward, pdet = pinnacle(conn, e["event_id"], market, at, pub)
                cst, cdet = circa(g, market, pub)
                rec.update(pinnacle={"status": pst, "toward_public": ptoward, **(pdet or {})},
                           circa={"status": cst, **(cdet or {})})
                if pst == "NO DATA":
                    rec["reason"] = "no Pinnacle price history"
                elif pst == "RESPECTED":
                    rec["reason"] = "Pinnacle moved toward the public side"
                elif cst == "RESPECTED":
                    rec["reason"] = "Circa moved toward the public side"
                else:
                    side = OPP[pub]
                    price = best_price(conn, e["event_id"], market, side, at)
                    if price is None:
                        rec["reason"] = "no fresh price for the play side at the snapshot"
                    else:
                        rec.update(status="PICK", play={"side": side, "strength": pst, **price},
                                   reason=f"public {pub}; Pinnacle {pst.lower()}; Circa {cst.lower()}")
            out.append(rec)
    return out


def grade(rec, final):
    """final = (home_score, away_score). W/L/P and units for a PLAY record, or None."""
    p = rec.get("play")
    if not p or final is None:
        return None
    h, a = final
    if rec["market"] == "spread":
        margin = (h - a) if p["side"] == "home" else (a - h)
        m = margin + p["line"]
    else:
        m = (h + a - p["line"]) * (1 if p["side"] == "over" else -1)
    res = "W" if m > 0 else "L" if m < 0 else "P"
    price = float(p["price"])
    units = (price / 100.0 if price > 0 else 100.0 / -price) if res == "W" else -1.0 if res == "L" else 0.0
    return {"result": res, "units": round(units, 3)}


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
