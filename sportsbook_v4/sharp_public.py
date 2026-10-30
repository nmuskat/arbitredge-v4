"""
sportsbook_v4/sharp_public.py -- SHARP VS PUBLIC: do our simulated sharps and our simulated recreational ("public")
customers put their money on opposite sides? (MODELED; descriptive only)

"Public" means this book's simulated recreational customers, never external sportsbook betting splits.
Everything is computed from accepted wagers in the immutable ledger, per game and per market (spread, total,
moneyline), each percentage over its own denominator:
  rec handle %   = the side's recreational stakes / all recreational stakes in that game-market
  sharp handle % = the side's sharp stakes / all sharp stakes in that game-market
  ticket %       = the same with wager counts, within each group
  sharp share    = all sharp stakes / ALL stakes in that game-market
Book Need is computed elsewhere from modeled P&L and is never influenced by this classification.

Classification (thresholds fixed here, not tuned to any outcome):
  INSUFFICIENT  fewer than MIN_SHARP_TICKETS sharp wagers, or fewer than MIN_SHARP_BETTORS distinct sharp customers,
                or sharp stakes under MIN_SHARP_SHARE of the game-market's handle, or one sharp customer holding more
                than MAX_ONE_SHARP_SHARE of the sharp handle, or fewer than MIN_REC_TICKETS recreational wagers
  MIXED         enough data, but a group has no clear handle majority (no side reaches MAJORITY of its own handle)
  SHARP_VS_PUBLIC  both groups have a clear majority, on opposite sides
  ALIGNED       both groups have a clear majority, on the same side
"""

import math

from sportsbook_v4.params import ARCHETYPES

SHARP = {a for a, v in ARCHETYPES.items() if v.get("is_sharp")}
SIDES = {"spread": ("home", "away"), "total": ("over", "under"), "moneyline": ("home", "away")}

MIN_SHARP_TICKETS = 3          # a single sharp wager (or two) can never create a signal
MIN_SHARP_BETTORS = 2          # ... nor can one sharp customer splitting a bet into several tickets
MAX_ONE_SHARP_SHARE = 0.70     # ... nor one customer holding most of the sharp money
MIN_SHARP_SHARE = 0.05         # sharp stakes must be at least 5% of the game-market's total handle
MIN_REC_TICKETS = 20
MAJORITY = 0.55                # a group "favours" a side only with at least 55% of its own handle there

LABELS = {"SHARP_VS_PUBLIC": "SHARP VS PUBLIC", "ALIGNED": "SHARP + PUBLIC ALIGNED",
          "MIXED": "MIXED / INSUFFICIENT DATA", "INSUFFICIENT": "MIXED / INSUFFICIENT DATA"}
THRESHOLDS = {"min_sharp_tickets": MIN_SHARP_TICKETS, "min_sharp_bettors": MIN_SHARP_BETTORS,
              "max_one_sharp_share": MAX_ONE_SHARP_SHARE, "min_sharp_share_of_handle": MIN_SHARP_SHARE,
              "min_rec_tickets": MIN_REC_TICKETS, "majority": MAJORITY}


def _pct(x, d):
    return round(100 * x / d, 1) if d else None


def _group(ws, sides):
    handle = math.fsum(w["stake"] for w in ws)
    out = {"tickets": len(ws), "handle": round(handle, 2), "bettors": len({w["bettor_index"] for w in ws}),
           "sides": {}}
    for s in sides:
        sel = [w for w in ws if w["side"] == s]
        h = math.fsum(w["stake"] for w in sel)
        out["sides"][s] = {"tickets": len(sel), "handle": round(h, 2),
                           "ticket_pct": _pct(len(sel), len(ws)), "handle_pct": _pct(h, handle)}
    fav = [s for s in sides if handle and out["sides"][s]["handle"] / handle >= MAJORITY]
    out["majority_side"] = fav[0] if fav else None
    return out


def market(wagers, m):
    """One game-market's sharp-vs-public record from its accepted wagers."""
    sides = SIDES[m]
    ws = [w for w in wagers if w["market"] == m]
    if not ws:
        return {"status": "NO_WAGERS", "label": "MODELED"}
    rec = _group([w for w in ws if w["archetype"] not in SHARP], sides)
    sh_ws = [w for w in ws if w["archetype"] in SHARP]
    sharp = _group(sh_ws, sides)
    total = math.fsum(w["stake"] for w in ws)
    per = {}
    for w in sh_ws:
        per[w["bettor_index"]] = per.get(w["bettor_index"], 0.0) + w["stake"]
    top_share = max(per.values()) / sharp["handle"] if sharp["handle"] else None
    sharp["largest_customer_share"] = round(top_share, 3) if top_share is not None else None
    why = []
    if sharp["tickets"] < MIN_SHARP_TICKETS:
        why.append(f"{sharp['tickets']} sharp wagers (min {MIN_SHARP_TICKETS})")
    if sharp["bettors"] < MIN_SHARP_BETTORS:
        why.append(f"{sharp['bettors']} sharp customers (min {MIN_SHARP_BETTORS})")
    if total and sharp["handle"] / total < MIN_SHARP_SHARE:
        why.append(f"sharp handle {_pct(sharp['handle'], total)}% of total (min {round(100 * MIN_SHARP_SHARE)}%)")
    if top_share is not None and top_share > MAX_ONE_SHARP_SHARE:
        why.append(f"one sharp customer holds {round(100 * top_share)}% of sharp handle (max {round(100 * MAX_ONE_SHARP_SHARE)}%)")
    if rec["tickets"] < MIN_REC_TICKETS:
        why.append(f"{rec['tickets']} recreational wagers (min {MIN_REC_TICKETS})")
    if why:
        cls = "INSUFFICIENT"
    elif not rec["majority_side"] or not sharp["majority_side"]:
        cls = "MIXED"
        why.append("no clear majority: " + ", ".join(g for g, x in (("recreational", rec), ("sharp", sharp))
                                                      if not x["majority_side"]))
    else:
        cls = "SHARP_VS_PUBLIC" if rec["majority_side"] != sharp["majority_side"] else "ALIGNED"
    return {"status": "OK", "label": "MODELED", "classification": cls, "display": LABELS[cls], "reasons": why,
            "total_tickets": len(ws), "total_handle": round(total, 2),
            "sharp_share_of_handle_pct": _pct(sharp["handle"], total),
            "recreational": rec, "sharp": sharp}


def game(wagers):
    return {m: market(wagers, m) for m in SIDES}
