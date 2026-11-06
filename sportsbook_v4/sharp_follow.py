"""
sportsbook_v4/sharp_follow.py -- SHARP FOLLOW LIST (EXPERIMENTAL research display, owner-requested 2026-11-06).

For every spread and total in the synthetic book where the SIMULATED sharps bet, the side holding more sharp
dollars, tagged with the patterns the owner reviewed after week 1 (Oct 27 - Nov 2):

  ANY              the side with more sharp dollars (every game-market with sharp money)
  ONE_SIDE         every sharp dollar on that side
  SPLIT            sharps bet both sides; the side with more sharp dollars
  SHARP_VS_PUBLIC  the Sharp vs Public card's label (sportsbook_v4.sharp_public): the sharps' majority side
  ALIGNED          the card's label: the side both groups' majorities are on

Records come only from the immutable synthetic-book ledger (wagers accepted before kickoff), final scores and the
last market line OBSERVED before kickoff (the number a follower could have taken; never a later price). They are
split at REGISTERED_AT: the patterns were chosen AFTER seeing week 1, so games before it are in-sample and flatter
them; only games kicking off after it test the patterns. Moneylines are excluded. Nothing here is an official play,
changes Book Need, Official Needs, thresholds or the ledger; the default decision stays NO BET.
"""

import math

from sportsbook_v4 import sharp_public

VERSION = "sharp_follow.v1"
REGISTERED_AT = "2026-11-06T23:45:00Z"       # patterns fixed (this file committed) before any game counted as forward
MARKETS = ("spread", "total")
SIDES = {"spread": ("away", "home"), "total": ("over", "under")}
TAGS = ("ANY", "ONE_SIDE", "SPLIT", "SHARP_VS_PUBLIC", "ALIGNED")
LABELS = {"ANY": "Bigger sharp side (all)", "ONE_SIDE": "Sharps on one side only",
          "SPLIT": "Sharps split: bigger side", "SHARP_VS_PUBLIC": "SHARP VS PUBLIC", "ALIGNED": "SHARP + PUBLIC ALIGNED"}


def tag(wagers, market):
    """The followed side and its tags for one game-market, or None when no simulated sharp bet it
    (or the sharps' dollars are exactly tied)."""
    ws = [w for w in wagers if w["market"] == market]
    sh = [w for w in ws if w["archetype"] in sharp_public.SHARP]
    if not sh:
        return None
    a, b = SIDES[market]
    h = {s: math.fsum(w["stake"] for w in sh if w["side"] == s) for s in (a, b)}
    if h[a] == h[b]:
        return None
    side = a if h[a] > h[b] else b
    tags = ["ANY", "ONE_SIDE" if min(h.values()) == 0 else "SPLIT"]
    card = sharp_public.market(ws, market)
    cls = card.get("classification")
    if cls == "SHARP_VS_PUBLIC" and card["sharp"]["majority_side"] == side:
        tags.append("SHARP_VS_PUBLIC")
    elif cls == "ALIGNED" and card["sharp"]["majority_side"] == side:
        tags.append("ALIGNED")
    return {"market": market, "side": side, "tags": tags, "card": cls,
            "sharp_handle": {s: round(v, 2) for s, v in h.items()},
            "sharp_tickets": {s: sum(1 for w in sh if w["side"] == s) for s in (a, b)},
            "sharp_bettors": len({w["bettor_index"] for w in sh}),
            "sharp_share_on_side": round(h[side] / (h[a] + h[b]), 3)}


def side_line(market, side, line):
    """The followed side's own number from a market line (spreads: the home line)."""
    if market == "spread":
        return line if side == "home" else -line
    return line


def grade(market, side, line, home_score, away_score):
    """W / L / P for `side` at its own number `line`."""
    if market == "spread":
        v = (home_score - away_score if side == "home" else away_score - home_score) + line
    else:
        v = (home_score + away_score - line) if side == "over" else (line - home_score - away_score)
    return "W" if v > 0 else "L" if v < 0 else "P"


def _empty():
    return {"W": 0, "L": 0, "P": 0, "no_line": 0}


def report(games, wagers_by_game, results, close_line, now_iso):
    """games: {(sport, event_id): {"sport", "event_id", "home", "away", "kickoff"}}; wagers_by_game: active-run
    wagers per (sport, event_id); results: {(sport, event_id): {"home_score", "away_score"}} for finished games;
    close_line(sport, event_id, market) -> the home spread / total last observed before kickoff, or None."""
    records = {}
    graded, upcoming = [], []
    for key in sorted(games, key=lambda k: (games[k]["kickoff"], k)):
        g = games[key]
        ko = g["kickoff"]
        ws = [w for w in wagers_by_game.get(key, []) if w["accepted_at"] < ko]
        for m in MARKETS:
            t = tag(ws, m)
            if t is None:
                continue
            row = {**{k: g[k] for k in ("sport", "event_id", "home", "away", "kickoff")}, **t}
            if ko > now_iso:
                upcoming.append(row)
                continue
            res = results.get(key)
            if res is None:
                continue                                            # awaiting a final score
            period = "forward" if ko >= REGISTERED_AT else "before"
            line = close_line(g["sport"], g["event_id"], m)
            out = None
            if line is not None:
                num = side_line(m, t["side"], line)
                out = grade(m, t["side"], num, res["home_score"], res["away_score"])
                row.update(line=num, result=out, home_score=res["home_score"], away_score=res["away_score"])
            row["period"] = period
            graded.append(row)
            for tg in t["tags"]:
                r = records.setdefault(g["sport"], {}).setdefault(m, {}).setdefault(tg, {
                    "before": _empty(), "forward": _empty()})[period]
                r[out if out else "no_line"] += 1
    return {"status": "OK", "label": "EXPERIMENTAL", "version": VERSION, "registered_at": REGISTERED_AT,
            "labels": LABELS, "tags": list(TAGS), "records": records,
            "graded": graded[-80:], "graded_total": len(graded), "upcoming": upcoming,
            "basis": "followed side graded at the market line last observed before kickoff (consensus of the "
                     "market-making books); simulated sharps' dollars from accepted wagers before kickoff",
            "note": "Patterns chosen after seeing week 1 (Oct 27 - Nov 2): records before registration are "
                    "in-sample and flatter them. Only games after registration test them. Not official plays; "
                    "default NO BET."}
