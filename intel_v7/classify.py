"""
intel_v7/classify.py -- module E (professional positioning classifier) and the ArbitrEdge MARKET NEED SIGNAL card.

Evidence items (each directional, relative to retail):
  A  material divergence (pro fair vs retail consensus)             1 pt, +1 if BOTH pro operators agree
  B  ACTIVE_RESISTANCE (both operators held)                        2 pts
     RESISTANCE_UNCORROBORATED / PRO_LED_MOVE                       1 pt
  C  KEY_DEFENSE                                                    1 pt
  D  convergence history for this sport/market: RETAIL_LEADS        -1 pt (professional divergence there has
                                                                     historically been absorbed by the pros)
Strength: WEAK 1, MODERATE 2-3, STRONG >= 4 (an ordinal evidence count, NOT a probability; no confidence is reported
because none has been empirically calibrated).
Classification: PROFESSIONAL PRICING LEANS HOME/AWAY/OVER/UNDER, CONFLICTING EVIDENCE, INSUFFICIENT DATA, or
NO MATERIAL DIFFERENCE (fresh data, professional and retail prices agree; added because "insufficient" would be false).
Headline: PROFESSIONAL PRICING INFERENCE only for a lean of at least MODERATE strength where the convergence history
does not show retail-led price discovery; any other lean is an OBSERVED MARKET DISAGREEMENT. SIGNAL only for the
former; everything else is PASS.
"""
from intel_v7 import LABEL, VERSION
from intel_v7 import modules as M
from intel_v7.config import CONFIG, CONFIG_HASH, STRENGTH_ORDER
from intel_v7.convergence import horizon_for
from intel_v7.history import view
from need_v6.market import normalizer
from sportsbook_v4.market import profit_on_win
from sportsbook_v4.util import from_epoch

LEAN = {"home": "PROFESSIONAL PRICING LEANS HOME", "away": "PROFESSIONAL PRICING LEANS AWAY",
        "over": "PROFESSIONAL PRICING LEANS OVER", "under": "PROFESSIONAL PRICING LEANS UNDER"}


def classify(fr, a, b, c, d):
    if fr["status"] != "FRESH" or a["status"] == "INSUFFICIENT":
        why = fr["status"] if fr["status"] != "FRESH" else a.get("reason")
        return {"classification": "INSUFFICIENT DATA", "strength": "NONE", "side": None, "evidence": [],
                "conflicts": [], "reason": why}
    items = []
    if a["status"] == "MATERIAL":
        items.append(("A divergence", a["side"], 2 if a.get("corroborated") else 1))
    if b["status"] == "ACTIVE_RESISTANCE":
        items.append(("B active resistance", b["side"], 2))
    elif b["status"] in ("RESISTANCE_UNCORROBORATED", "PRO_LED_MOVE"):
        items.append(("B " + b["status"].lower().replace("_", " "), b["side"], 1))
    if c["status"] == "KEY_DEFENSE":
        items.append(("C key-number defense", c["side"], 1))
    conflicts = []
    if a["status"] == "PROS_DISAGREE":
        conflicts.append("Pinnacle and the BetOnline group differ from retail in opposite directions")
    sides = {s for _, s, _ in items}
    if len(sides) > 1:
        conflicts.append("modules point to different sides: " + "; ".join(f"{n} -> {s}" for n, s, _ in items))
    if conflicts:
        return {"classification": "CONFLICTING EVIDENCE", "strength": "NONE", "side": None,
                "evidence": [{"module": n, "side": s, "points": p} for n, s, p in items], "conflicts": conflicts}
    if not items:
        return {"classification": "NO MATERIAL DIFFERENCE", "strength": "NONE", "side": None, "evidence": [],
                "conflicts": [], "reason": "fresh professional and retail prices agree within the material threshold"}
    pts = sum(p for _, _, p in items)
    if d.get("status") == "RETAIL_LEADS":
        pts -= 1
    strength = "STRONG" if pts >= 4 else "MODERATE" if pts >= 2 else "WEAK" if pts >= 1 else "NONE"
    side = next(iter(sides))
    return {"classification": LEAN[side], "strength": strength, "side": side, "points": pts,
            "evidence": [{"module": n, "side": s, "points": p} for n, s, p in items], "conflicts": []}


def best_price(hist, t, side, pro_mu):
    """Best observed executable price for `side` among books quoted in the decision fetch, valued at the
    professional fair (EV per unit staked). Executable = it was on the board at that fetch; it may have moved since."""
    ft, books = hist.snap_at(t)
    nz = normalizer(hist.sport, hist.market)
    best = None
    for bk, q in books.items():
        if q["mu"] is None:
            continue
        pt = q["point"] if (hist.market == "total" or side == "home") else -q["point"]
        price = q["price_a"] if side in ("home", "over") else q["price_b"]
        pw, pp = nz.prob_at(pro_mu, side, pt)
        ev = pw * profit_on_win(1.0, price) - (1 - pw - pp)
        if best is None or ev > best["ev_vs_pro_fair"] + 1e-12:
            best = {"book": bk, "point": pt, "price": price, "ev_vs_pro_fair": round(ev, 4), "observed_at": from_epoch(ft)}
    return best


def card(hist, t, conv, game_label=None):
    fr = M.freshness(hist, t)
    a = M.divergence(hist, t)
    b = M.resistance(hist, t)
    c = M.key_numbers(hist, t)
    hours = (hist.kickoff - t) / 3600 if hist.kickoff else None
    d = conv.stats(hist.sport, hist.market, horizon_for(hours), t) if (conv is not None and hours is not None) else \
        {"status": "INSUFFICIENT", "reason": "no convergence history"}
    e = classify(fr, a, b, c, d)
    side = e["side"]
    infer = side is not None and STRENGTH_ORDER.index(e["strength"]) >= STRENGTH_ORDER.index(CONFIG["signal_min_strength"]) \
        and d.get("status") != "RETAIL_LEADS"
    headline = ("PROFESSIONAL PRICING INFERENCE" if infer else "OBSERVED MARKET DISAGREEMENT" if side else
                e["classification"])
    names = {"home": hist.home, "away": hist.away, "over": "Over", "under": "Under"}
    ft, books = hist.snap_at(t)
    v = view(books)
    retail_pts = sorted({q["point"] for bk, q in books.items() if bk in CONFIG["retail_books"]})
    bp = best_price(hist, t, side, a["pro_mu"]) if side and "pro_mu" in a else None
    if side:
        sig_text = f"{headline}: professional pricing leans {names[side]} relative to retail"
    else:
        sig_text = headline
    return {
        "version": VERSION, "config_hash": CONFIG_HASH, "label": LABEL, "as_of": from_epoch(t),
        "sport": hist.sport, "event_id": hist.event_id, "market": hist.market,
        "game": game_label or f"{hist.away} @ {hist.home}", "home": hist.home, "away": hist.away,
        "kickoff": from_epoch(hist.kickoff) if hist.kickoff else None,
        "professional_pricing_side": names[side] if side else "NONE",
        "side": side,
        "market_need_signal": sig_text,
        "headline_type": headline,
        "classification": e["classification"],
        "evidence_strength": e["strength"],
        "evidence": e["evidence"],
        "professional_price": {"basis": a.get("pro_basis"), "fair_mu": a.get("pro_mu"),
                               "quotes": {bk: q for bk, q in (a.get("quotes") or {}).items()
                                          if bk in ("pinnacle", "betonlineag", "lowvig")}},
        "retail_consensus": {"fair_mu": a.get("retail_mu"), "books": v.get("retail_books"), "points": retail_pts,
                             "dispersion_pts": a.get("retail_dispersion_pts")},
        "divergence": a,
        "key_number_analysis": c,
        "active_resistance": b,
        "market_convergence": d,
        "quote_freshness": fr,
        "conflicting_evidence": e["conflicts"] or "NONE",
        "actual_book_liability": "UNKNOWN",
        "best_observed_executable_price": bp,
        "validation_status": "UNVALIDATED: development rules; prospective evaluation per docs_v4/V7_PROSPECTIVE_SPEC.md",
        "signal_or_pass": "SIGNAL" if infer else "PASS",
        "pass_reason": None if infer else (
            "insufficient or stale data" if e["classification"] == "INSUFFICIENT DATA" else
            "conflicting evidence" if e["classification"] == "CONFLICTING EVIDENCE" else
            "no material difference" if not side else
            "retail-led price discovery in this sport/market" if d.get("status") == "RETAIL_LEADS" else
            f"evidence strength {e['strength']} below {CONFIG['signal_min_strength']}"),
    }
