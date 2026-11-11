"""
need_v6/need.py -- the four NEED fields of one game-market card, kept strictly separate.

FINANCIAL NEED      outcome that maximises the synthetic book's P&L from wagers ALREADY accepted (existing
                    sportsbook_v4.settlement code, each wager at its own number/price/stake), plus materiality and
                    expected book P&L under the fair outcome distribution.
MARKET-MAKER INFER. observable pricing of Pinnacle vs recreational books (normalised); pricing_opinion_side when
                    readable; candidate_need_side is ALWAYS "UNKNOWN" (odds do not reveal a book's liability).
TRADING DESK NEED   the side the modelled desk deliberately chose to be exposed toward (ACCEPT EXPOSURE), else NONE.
ARBITREDGE NEED     official headline, policy `desk_material_first_v1` (deterministic, documented):
                      1. Trading Desk NEED exists (ACCEPT EXPOSURE) and its position is MATERIAL -> source TRADING DESK
                      2. else Financial NEED is MATERIAL                                       -> source FINANCIAL
                      3. else                                                                   -> NONE
                    The source is always printed; one type is never silently substituted for another.
None of these is real sportsbook liability or a betting recommendation.
"""

from need_v6 import LABEL, VERSION
from need_v6.config import CONFIG, CONFIG_HASH
from need_v6.market import SIDES, pmf_on_grid
from sportsbook_v4 import settlement as ST
from sportsbook_v4.util import from_epoch


def _team(game_names, side):
    return game_names.get(side, side)


def side_text(market, side, point, names):
    if side is None:
        return "NONE"
    if market == "spread":
        return f"{_team(names, side)} {'+' if point > 0 else ''}{point:g}"
    return f"{'Over' if side == 'over' else 'Under'} {point:g}"


def financial(wagers, market, exposure, gm, ms):
    ws = [dict(w, accepted_home_spread=w["accepted_home_spread"]) for w in wagers]
    if market == "spread":
        ex = ST.exposure(ws)
        pa, pb = ex.get("pnl_if_home_covers_all"), ex.get("pnl_if_away_covers_all")
    else:
        ex = ST.total_exposure(ws)
        pa, pb = ex.get("pnl_if_over_all"), ex.get("pnl_if_under_all")
    handle = ex.get("handle", 0.0) or 0.0
    swing = ex.get("pnl_swing") or 0.0
    material = bool(ex.get("financial_need_side")) and swing >= max(CONFIG["materiality_abs"],
                                                                    CONFIG["materiality_share"] * handle)
    status = "MATERIAL" if material else ("MINOR" if ex.get("financial_need_side") else "BALANCED")
    ev = sd = None
    if ms.get("fair_mu") is not None and exposure.n:
        ev, sd = exposure.stats(pmf_on_grid(gm, ms["fair_mu"], market))
    return {"side": ex.get("financial_need_side"), "status": status,
            "pnl_if_side_a_covers_all": pa, "pnl_if_side_b_covers_all": pb, "swing": swing, "handle": handle,
            "tickets": ex.get("tickets", 0), "worst_case": round(exposure.worst(), 2),
            "middle_risk": ex.get("middle_risk_margins") or ex.get("middle_risk_totals") or [],
            "expected_pnl_at_fair": None if ev is None else round(ev, 2), "pnl_sd_at_fair": None if sd is None else round(sd, 2)}


def market_maker(gm, ms):
    if ms.get("status") != "OK":
        return {"status": ms.get("status", "NO_DATA"), "pricing_opinion_side": "UNKNOWN", "candidate_need_side": "UNKNOWN"}
    fresh = ms["fresh"]
    pin = fresh.get("pinnacle")
    a, b = SIDES[gm.market]
    out = {"status": "OK", "fair_basis": ms["basis"], "fair_mu": round(ms["fair_mu"], 2),
           "uncertainty_pts": ms["uncertainty"], "rec_mu": None if ms.get("rec_mu") is None else round(ms["rec_mu"], 2),
           "fresh_books": sorted(fresh), "observed_at": from_epoch(ms["obs_at"]), "candidate_need_side": "UNKNOWN"}
    if pin and ms.get("rec_mu") is not None:
        gap = pin["mu"] - ms["rec_mu"]
        out["pinnacle_vs_rec_gap_pts"] = round(gap, 2)
        out["pricing_opinion_side"] = (a if gap > 0 else b) if abs(gap) >= 0.5 else "UNKNOWN"
        out["observed"] = (f"Pinnacle prices {'home' if gm.market == 'spread' else 'the total'} "
                           f"{abs(gap):.2f} pts {'higher' if gap > 0 else 'lower'} than the recreational median"
                           if abs(gap) >= 0.5 else "Pinnacle and recreational books agree within 0.5 pt")
        out["key_number"] = gm.market == "spread" and abs(pin["point"]) in CONFIG["key_numbers"]
    else:
        out["pricing_opinion_side"] = "UNKNOWN"
        out["observed"] = "Pinnacle not quoted at this observation" if not pin else "no recreational quotes"
    out["interpretation"] = ("Observable pricing only. Whether any professional book wants to NEED a side cannot be "
                             "identified from odds: UNKNOWN.")
    return out


def card(k, t, gm, desk, exposure, wagers, game, names):
    ms = gm.state(t)
    market = gm.market
    fin = financial(wagers, market, exposure, gm, ms)
    d = desk.decision
    offers = desk.offers(ms.get("fair_mu")) if ms.get("status") == "OK" else {}
    desk_side = d.get("desk_need")
    desk_material = desk_side is not None and d.get("swing", 0) >= max(CONFIG["materiality_abs"],
                                                                      CONFIG["materiality_share"] * exposure.handle)
    a, b = SIDES[market]

    def pt_of(side):
        if side in offers:
            return offers[side][0]
        if desk.line is None:
            return None
        return gm.side_point(side, desk.line)

    if desk_side and desk_material:
        off_side, source, rule = desk_side, "TRADING DESK", "1: deliberate desk position (ACCEPT EXPOSURE), material"
    elif fin["status"] == "MATERIAL":
        off_side, source, rule = fin["side"], "FINANCIAL", "2: no material desk position; material financial NEED"
    else:
        off_side, source, rule = None, "NONE", "3: no material desk position or financial NEED"
    p = pt_of(off_side) if off_side else None
    price = offers.get(off_side, (None, None))[1] if off_side else None
    return {
        "version": VERSION, "config_hash": CONFIG_HASH, "label": LABEL, "as_of": from_epoch(t),
        "event_id": k[0], "market": market, "home": names["home"], "away": names["away"],
        "kickoff": game.commence_time,
        "arbitredge_need": {"text": side_text(market, off_side, p, names) if off_side else "NONE", "side": off_side,
                            "point": p, "price": price, "source": source, "rule": rule,
                            "policy": CONFIG["official_policy"]},
        "financial_need": {**fin, "text": side_text(market, fin["side"], pt_of(fin["side"]), names) if fin["side"] else "NONE"},
        "trading_desk_need": {"side": desk_side, "text": side_text(market, desk_side, pt_of(desk_side), names)
                              if desk_side else "NONE", "action": d.get("action"), "rationale": d.get("rationale"),
                              "decided_at": from_epoch(d["at"]) if d.get("at") else None,
                              "risk_used": d.get("risk_used"), "uncertainty_pts": d.get("uncertainty_pts"),
                              "material": desk_material,
                              "basis": "risk acceptance (no validated informational edge)" if desk_side else None},
        "market_maker_inference": market_maker(gm, ms),
        "book": {"posted_line": desk.line, "offers": {s: {"point": v[0], "price": v[1]} for s, v in offers.items()},
                 "closed_sides": sorted(desk.closed), "handle": round(exposure.handle, 2),
                 "side_handle": {s: round(v, 2) for s, v in exposure.side_handle.items()},
                 "worst_case": round(exposure.worst(), 2),
                 "risk_used": round(-exposure.worst() / CONFIG["game_limit"][gm.sport], 3) if exposure.worst() < 0 else 0.0,
                 "game_limit": CONFIG["game_limit"][gm.sport]},
        "market_state": {"status": ms.get("status"), "line": ms.get("line"), "fair_mu": None if ms.get("fair_mu") is None
                   else round(ms["fair_mu"], 2), "observed_at": from_epoch(ms["obs_at"]) if ms.get("obs_at") else None},
    }
