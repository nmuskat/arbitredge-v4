"""
official_v5/strategies.py -- Official ArbitrEdge Needs strategies after official_needs.v1 (EXPERIMENTAL MODEL SIGNALS).

  official_needs.spread.v2   spreads (home/away)        } retired 2026-10-31: records kept, sealed and graded
  official_needs.total.v1    game totals (over/under)   } under their own rules
  official_needs.spread.v3   spreads, Public Pressure at >= 60% of recreational handle (active)
  official_needs.total.v2    totals, Public Pressure at >= 60% of recreational handle (active)

Each strategy applies six rules to its OWN market, with that market's Book Need (modeled P&L), handle and liability
rankings computed separately per market:
  stable_need     (MANDATORY) the market's Book Need side is the same under the baseline and all 4 sensitivity assumptions
  data_valid      (MANDATORY) directional Need, priced offer, odds <= 3 h old, ledger <= 3 h behind the build, within 24 h
                  of kickoff, >= 100 distinct bettors in the market, no bettor above 15% of the market's handle
  top_handle      High Handle: the market's handle ranks in the top 25% of the week's games
  rec_opposes     Public Pressure: >= 65% (v3/v2: >= 60%) of recreational handle and > 50% of recreational tickets
                  against the Need
  sharp_supports  Sharp Alignment: >= 60% of sharp handle on the Need side; >= 3 sharp wagers, >= 2 sharp customers,
                  no sharp customer above 70% of sharp handle, sharp handle >= 5% of the market's handle
  top_swing       High Liability: the book's P&L swing in the market ranks in the top 25% of the week's directional games
OFFICIAL (an EXPERIMENTAL MODEL SIGNAL; the site's decision stays NO BET): both mandatory rules and at least 3 of the
other 4. 6/6 = PERFECT, 5/6 = STANDARD (the failed rule is named). WATCHLIST: both mandatory and exactly 2 of the other 4.
PASS: anything else. Thresholds are the official_needs.v1 values; each strategy's rule set is hashed into every record
and pinned by a test, so a change needs a new version. Records, sealing and grading are the same immutable,
prospective machinery as v1 (official_v5.rules), keyed by strategy so versions never mix.
"""

import math

from sportsbook_v4 import need_tracker as NT
from sportsbook_v4.settlement import total_exposure
from sportsbook_v4.util import json_hash, to_epoch
from official_v5 import rules as V1

# when each strategy's rules were fixed: no record of a strategy is ever created before its time
REGISTERED = {"official_needs.spread.v2": "2026-10-31T05:00:00Z", "official_needs.total.v1": "2026-10-31T05:00:00Z",
              "official_needs.spread.v3": "2026-10-31T14:45:00Z", "official_needs.total.v2": "2026-10-31T14:45:00Z"}
OTHERS = ("top_handle", "rec_opposes", "sharp_supports", "top_swing")
MANDATORY = ("stable_need", "data_valid")


def _rules(market, min_rec_handle_share=0.65):
    return {"market": market,
            "stable_need": {"variants_required": 4},
            "top_handle": {"top_share": 0.25},
            "rec_opposes": {"min_rec_handle_share": min_rec_handle_share, "min_rec_ticket_share_exclusive": 0.50},
            "sharp_supports": {"min_sharp_handle_share": 0.60, "min_sharp_wagers": 3, "min_sharp_bettors": 2,
                               "max_one_sharp_share": 0.70, "min_sharp_share_of_handle": 0.05},
            "top_swing": {"top_share": 0.25},
            "data_valid": {"max_odds_age_h": 3, "max_ledger_lag_h": 3, "max_hours_before_kickoff": 24,
                           "min_distinct_bettors": 100, "max_largest_bettor_share": 0.15},
            "official": {"mandatory": list(MANDATORY), "min_other_passes": 3},
            "watchlist": {"mandatory": list(MANDATORY), "min_other_passes": 2},
            "seal": {"final_record_max_minutes_before_kickoff": 180}}


# earlier strategies: rules unchanged (hash pinned); their records are still sealed and graded, no new ones are made
RETIRED = {"official_needs.spread.v2": _rules("spread"), "official_needs.total.v1": _rules("total")}
# active strategies (owner request 2026-10-31): identical except Public Pressure at >= 60% of recreational handle
STRATEGIES = {"official_needs.spread.v3": _rules("spread", 0.60), "official_needs.total.v2": _rules("total", 0.60)}
ALL = {**RETIRED, **STRATEGIES}
HASHES = {k: json_hash(v) for k, v in ALL.items()}
SIDES = {"spread": ("home", "away"), "total": ("over", "under")}
OPPOSITE = {"home": "away", "away": "home", "over": "under", "under": "over"}
LABELS = {"stable_need": "Stable Book Need (all 4 sensitivity assumptions)", "data_valid": "Valid, fresh data and bettors",
          "top_handle": "High Handle (top 25% of the week)", "rec_opposes": "Public Pressure (recreational money opposes)",
          "sharp_supports": "Sharp Alignment (sharp money supports)", "top_swing": "High Liability (top 25% P&L swing)"}
ORDER = ("stable_need", "top_handle", "rec_opposes", "sharp_supports", "top_swing", "data_valid")


def _need(ws, market):
    """The market's Book Need from the book's modeled P&L (the existing calculations, unchanged)."""
    if not ws:
        return {"status": "NO_WAGERS", "side": None, "swing": None}
    if market == "spread":
        n = NT.need_of(ws)
        return {"status": n.get("status"), "side": n.get("side"), "swing": n.get("swing"), "liability": n.get("liability"),
                "worst_case_pnl": n.get("worst_case_pnl")}
    t = total_exposure(ws)
    return {"status": t.get("status"), "side": t.get("financial_need_side"), "swing": t.get("pnl_swing"),
            "liability": min(t["pnl_if_over_all"], t["pnl_if_under_all"]) if t.get("status") == "DIRECTIONAL" else None,
            "worst_case_pnl": None}


def market_wagers(wagers, market):
    return [w for w in wagers if w["market"] == market]


def week_inputs(wagers_by_game, market):
    """Per-week, per-market inputs: handle of every game, P&L swing of every game with a directional Need."""
    handles, swings = {}, {}
    for g, ws in wagers_by_game.items():
        mw = market_wagers(ws, market)
        if not mw:
            continue
        handles[g] = math.fsum(w["stake"] for w in mw)
        n = _need(mw, market)
        if n["status"] == "DIRECTIONAL" and n["swing"] is not None:
            swings[g] = n["swing"]
    return {"handles": handles, "swings": swings}


def stability(base_side, variants, key):
    """Need stability for one market: variants = {name: split dict}, key = '_need' (spread) or '_need_total'."""
    sides = {k: v.get(key) for k, v in sorted(variants.items())}
    if base_side is None or not sides or any(key not in variants[k] for k in sides):
        return {"baseline": base_side, "variants": len(sides) if all(key in v for v in variants.values()) else 0,
                "agree": None, "label": None}
    agree = sum(x == base_side for x in sides.values())
    return {"baseline": base_side, "variants": len(sides), "agree": agree, "share": round(agree / len(sides), 2),
            "flips_under": [k for k, x in sides.items() if x != base_side],
            "label": "STABLE" if agree == len(sides) else "FRAGILE"}


def _pressure(ws, sides):
    """Recreational shares within recreational money; bettors and concentration within the market."""
    rec = [w for w in ws if w["archetype"] not in NT.SHARP]
    rh = math.fsum(w["stake"] for w in rec)
    total = math.fsum(w["stake"] for w in ws)
    per = {}
    for w in ws:
        per[w["bettor_index"]] = per.get(w["bettor_index"], 0.0) + w["stake"]
    out = {"wagers": len(ws), "handle": round(total, 2), "bettors": len(per),
           "largest_bettor_share": round(max(per.values()) / total, 4) if total else None, "sides": {}}
    for s in sides:
        sel = [w for w in rec if w["side"] == s]
        out["sides"][s] = {"rec_tickets": len(sel), "rec_handle": round(math.fsum(w["stake"] for w in sel), 2),
                           "rec_ticket_share": round(len(sel) / len(rec), 4) if rec else None,
                           "rec_handle_share": round(math.fsum(w["stake"] for w in sel) / rh, 4) if rh else None}
    return out


def _pick(market, side, offer, game):
    if not (offer and side):
        return None, None, None
    if market == "spread":
        line = offer["home_spread"] if side == "home" else -offer["home_spread"]
        return game.get(side), line, offer["home_price"] if side == "home" else offer["away_price"]
    return side.upper(), offer.get("total"), offer.get("over_price") if side == "over" else offer.get("under_price")


def evaluate(strategy, *, game, wagers, week, need_stability, offer, odds_as_of, taken_at, built_at,
             enforce_window=True):
    """Rule results for one game in the strategy's market (pure; no side effects)."""
    R = ALL[strategy]
    m = R["market"]
    kick = to_epoch(game["kickoff"])
    ws = market_wagers(wagers, m)
    nd = _need(ws, m)
    side = nd["side"]
    other = OPPOSITE.get(side)
    p = _pressure(ws, SIDES[m]) if ws else None
    rules = {}

    ns = need_stability or {}
    rules["stable_need"] = {"pass": bool(side) and ns.get("label") == "STABLE" and ns.get("baseline") == side
                            and ns.get("variants") == R["stable_need"]["variants_required"],
                            "value": f"{ns.get('agree')}/{ns.get('variants')} assumptions agree" if ns.get("agree") is not None
                            else "not available"}
    hr, hn = V1._rank(week["handles"], game["event_id"])
    cut = max(1, math.ceil(hn * R["top_handle"]["top_share"])) if hn else 0
    rules["top_handle"] = {"pass": hr is not None and hr <= cut, "value": f"{m} handle rank {hr} of {hn} (top {cut})" if hr
                           else "no handle"}
    if p and other:
        rh, rt = p["sides"][other]["rec_handle_share"], p["sides"][other]["rec_ticket_share"]
        t = R["rec_opposes"]
        rules["rec_opposes"] = {"pass": rh is not None and rt is not None and rh >= t["min_rec_handle_share"]
                                and rt > t["min_rec_ticket_share_exclusive"],
                                "value": f"{V1._pct(rh)} of rec handle, {V1._pct(rt)} of rec tickets against the Need"}
    else:
        rules["rec_opposes"] = {"pass": False, "value": "no directional Need"}
    sx = V1._sharp(ws, side) if side else None
    t = R["sharp_supports"]
    if sx:
        why = []
        if sx["sharp_wagers"] < t["min_sharp_wagers"]:
            why.append(f"{sx['sharp_wagers']} sharp wagers (min {t['min_sharp_wagers']})")
        if sx["sharp_bettors"] < t["min_sharp_bettors"]:
            why.append(f"{sx['sharp_bettors']} sharp customers (min {t['min_sharp_bettors']})")
        if sx["largest_sharp_share"] is not None and sx["largest_sharp_share"] > t["max_one_sharp_share"]:
            why.append(f"one sharp customer {V1._pct(sx['largest_sharp_share'])} of sharp handle (max {V1._pct(t['max_one_sharp_share'])})")
        if (sx["sharp_share_of_handle"] or 0) < t["min_sharp_share_of_handle"]:
            why.append(f"sharp handle {V1._pct(sx['sharp_share_of_handle'])} of the market (min {V1._pct(t['min_sharp_share_of_handle'])})")
        ok = not why and (sx["need_side_share"] or 0) >= t["min_sharp_handle_share"]
        rules["sharp_supports"] = {"pass": ok, "value": f"{V1._pct(sx['need_side_share'])} of sharp handle on the Need side"
                                   + (" (" + "; ".join(why) + ")" if why else "")}
    else:
        rules["sharp_supports"] = {"pass": False, "value": "no directional Need"}
    sr, sn = V1._rank(week["swings"], game["event_id"])
    cut = max(1, math.ceil(sn * R["top_swing"]["top_share"])) if sn else 0
    directional = nd["status"] == "DIRECTIONAL" and nd["swing"] is not None
    rules["top_swing"] = {"pass": directional and sr is not None and sr <= cut,
                          "value": f"{m} swing ${nd['swing']:,.0f}, rank {sr} of {sn} (top {cut})" if directional and sr
                          else "no directional Need"}
    team, line, price = _pick(m, side, offer, game)
    t = R["data_valid"]
    why = []
    if nd["status"] != "DIRECTIONAL":
        why.append(f"Need {nd['status']}")
    if line is None or price is None:
        why.append("no priced market offer")
    if odds_as_of is None or taken_at - odds_as_of > t["max_odds_age_h"] * 3600:
        why.append("odds older than 3 h")
    if built_at - taken_at > t["max_ledger_lag_h"] * 3600:
        why.append("ledger more than 3 h behind the build")
    if not (taken_at < kick and built_at < kick):
        why.append("not before kickoff")
    elif enforce_window and kick - taken_at > t["max_hours_before_kickoff"] * 3600:
        why.append("more than 24 h before kickoff")
    if not p or p["bettors"] < t["min_distinct_bettors"]:
        why.append(f"{p['bettors'] if p else 0} distinct {m} bettors (min {t['min_distinct_bettors']})")
    elif (p["largest_bettor_share"] or 0) > t["max_largest_bettor_share"]:
        why.append(f"largest bettor {V1._pct(p['largest_bettor_share'])} of {m} handle (max {V1._pct(t['max_largest_bettor_share'])})")
    rules["data_valid"] = {"pass": not why, "value": "; ".join(why) or "fresh and valid"}

    mand = all(rules[k]["pass"] for k in MANDATORY)
    others = sum(rules[k]["pass"] for k in OTHERS)
    score = sum(rules[k]["pass"] for k in ORDER)
    if mand and others >= R["official"]["min_other_passes"]:
        cls, tier = "OFFICIAL", ("PERFECT" if score == 6 else "STANDARD")
    elif mand and others >= R["watchlist"]["min_other_passes"]:
        cls, tier = "WATCHLIST", None
    else:
        cls, tier = "PASS", None
    return {"classification": cls, "tier": tier, "score": f"{score}/6", "market": m,
            "pick": {"side": side, "team": team, "line": line, "price": price,
                     "price_book": (offer or {}).get("price_book"), "offer": offer},
            "rules": [{"id": k, "name": LABELS[k], "mandatory": k in MANDATORY, **rules[k]} for k in ORDER],
            "failed": [LABELS[k] for k in ORDER if not rules[k]["pass"]],
            "supporting": {"need": nd, "pressure": p, "sharp": sx, "need_stability": ns,
                           "week_handle_rank": hr, "week_games": hn, "swing_rank": sr, "swing_games": sn,
                           "odds_as_of": V1._iso(odds_as_of) if odds_as_of else None}}


def snapshot(strategy, *, sport, run, game, evaluation, taken_at, built_at):
    """One immutable pre-kickoff record of this strategy (only at/after REGISTERED_AT and before kickoff)."""
    kick = to_epoch(game["kickoff"])
    if not (taken_at < kick and built_at < kick):
        raise ValueError("an Official Needs record must be taken before kickoff")
    if taken_at < to_epoch(REGISTERED[strategy]):
        raise ValueError("before the rules were registered: never retroactive")
    rec = {"format": strategy, "strategy": strategy, "rules_hash": HASHES[strategy], "label": V1.LABEL, "sport": sport,
           "event_id": game["event_id"], "home": game["home"], "away": game["away"], "kickoff": V1._iso(kick),
           "taken_at": V1._iso(taken_at), "built_at": V1._iso(built_at),
           "minutes_before_kickoff": round((kick - taken_at) / 60),
           "run_id": run["run_id"], "engine_version": run["engine_version"], "params_hash": run["params_hash"],
           **evaluation}
    rec = NT._norm(rec)
    rec["record_hash"] = json_hash(rec)
    return rec
