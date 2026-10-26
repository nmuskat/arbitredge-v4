"""
sportsbook_v4/settlement.py -- settle individual wagers at THEIR accepted
line and price, and build the book's exposure from them.

Pure functions; no database access.

Key identity: every spread wager on a game pushes at final home margin
    m* = -accepted_home_spread
(home side at line L=hs: home margin + L = 0  -> m = -hs;
 away side at line L=-hs: -m + L = 0          -> m = -hs).
So the book's P&L as a function of the final home margin is a step
function whose only breakpoints are the distinct accepted home spreads.
Evaluating it on every integer margin across the breakpoints (plus one
'clean' margin beyond each end) is therefore EXACT -- no margin
distribution or game model is assumed anywhere in this module.
"""

import math
from statistics import median

from sportsbook_v4.market import profit_on_win

CLEAN_MARGIN_PAD = 1000  # a margin beyond every breakpoint: every bet on one side wins


def wager_result(side, accepted_line, home_margin):
    m = home_margin if side == "home" else -home_margin
    x = m + accepted_line
    return "win" if x > 0 else ("push" if x == 0 else "loss")


def book_pnl(w, home_margin):
    r = wager_result(w["side"], w["accepted_line"], home_margin)
    if r == "win":
        return -profit_on_win(w["stake"], w["accepted_price"])
    if r == "loss":
        return float(w["stake"])
    return 0.0


def book_pnl_total(wagers, home_margin):
    return round(math.fsum(book_pnl(w, home_margin) for w in wagers), 2)


def breakpoints(wagers):
    return sorted({-float(w["accepted_home_spread"]) for w in wagers})


def margin_grid(wagers):
    bps = breakpoints(wagers)
    if not bps:
        return []
    return list(range(math.floor(bps[0]) - 1, math.ceil(bps[-1]) + 2))


def pnl_curve(wagers):
    return [(m, book_pnl_total(wagers, m)) for m in margin_grid(wagers)]


def _stake_stats(stakes):
    if not stakes:
        return None
    s = sorted(stakes)
    return {
        "count": len(s), "total": round(math.fsum(s), 2), "mean": round(math.fsum(s) / len(s), 2),
        "median": round(median(s), 2), "p90": round(s[min(len(s) - 1, int(0.9 * len(s)))], 2),
        "max": round(s[-1], 2),
    }


def exposure(wagers, reference_home_spread=None):
    """Full exposure summary for one game's accepted SPREAD wagers (other
    markets are ignored here; see book_need_all for every market)."""
    wagers = [w for w in wagers if w.get("market", "spread") == "spread"]
    home = [w for w in wagers if w["side"] == "home"]
    away = [w for w in wagers if w["side"] == "away"]
    hh = round(math.fsum(w["stake"] for w in home), 2)
    ah = round(math.fsum(w["stake"] for w in away), 2)
    handle = round(hh + ah, 2)
    n = len(wagers)
    out = {
        "tickets": n, "home_tickets": len(home), "away_tickets": len(away),
        "handle": handle, "home_handle": hh, "away_handle": ah,
        "home_ticket_pct": round(100.0 * len(home) / n, 2) if n else None,
        "away_ticket_pct": round(100.0 * len(away) / n, 2) if n else None,
        "home_handle_pct": round(100.0 * hh / handle, 2) if handle else None,
        "away_handle_pct": round(100.0 * ah / handle, 2) if handle else None,
    }
    if not wagers:
        out.update({"financial_need_side": None, "status": "NO_WAGERS"})
        return out

    bps = breakpoints(wagers)
    hi_m, lo_m = bps[-1] + CLEAN_MARGIN_PAD, bps[0] - CLEAN_MARGIN_PAD
    pnl_home_all = book_pnl_total(wagers, hi_m)   # home covers every accepted number
    pnl_away_all = book_pnl_total(wagers, lo_m)   # away covers every accepted number
    curve = pnl_curve(wagers)
    values = [p for _, p in curve] + [pnl_home_all, pnl_away_all]
    worst, best = min(values), max(values)
    lo_clean, hi_clean = min(pnl_home_all, pnl_away_all), max(pnl_home_all, pnl_away_all)

    if pnl_home_all == pnl_away_all:
        need, status = None, "TIE"
    else:
        need, status = ("home" if pnl_home_all > pnl_away_all else "away"), "DIRECTIONAL"

    out.update({
        "status": status,
        "financial_need_side": need,
        "pnl_if_home_covers_all": pnl_home_all,
        "pnl_if_away_covers_all": pnl_away_all,
        "pnl_swing": round(abs(pnl_home_all - pnl_away_all), 2),
        "hold_pct_if_home_covers_all": round(100.0 * pnl_home_all / handle, 3) if handle else None,
        "hold_pct_if_away_covers_all": round(100.0 * pnl_away_all / handle, 3) if handle else None,
        "worst_case_pnl": worst,
        "worst_case_margins": [m for m, p in curve if p == worst] or None,
        "best_case_pnl": best,
        "push_margins": [m for m in bps if float(m).is_integer()],
        "middle_risk_margins": [m for m, p in curve if p < lo_clean],      # book loses more than either clean cover
        "middle_benefit_margins": [m for m, p in curve if p > hi_clean],   # book wins more than either clean cover
        "pnl_by_home_margin": curve,
        "accepted_numbers": _accepted_numbers(wagers),
        "by_archetype": _by_archetype(wagers, hi_m, lo_m),
        "concentration": _concentration(wagers, handle),
    })
    if reference_home_spread is not None:
        out["reference"] = reference_scenarios(wagers, reference_home_spread)
    return out


def reference_scenarios(wagers, ref_hs):
    """P&L when the final margin lands just on each side of a reference line."""
    threshold = -float(ref_hs)              # home covers the reference if margin > threshold
    home_narrow = math.floor(threshold) + 1
    away_narrow = math.ceil(threshold) - 1
    res = {
        "reference_home_spread": ref_hs,
        "home_narrow_cover_margin": home_narrow, "pnl_home_narrow_cover": book_pnl_total(wagers, home_narrow),
        "away_narrow_cover_margin": away_narrow, "pnl_away_narrow_cover": book_pnl_total(wagers, away_narrow),
    }
    if float(threshold).is_integer():
        res["push_margin"] = int(threshold)
        res["pnl_at_reference_push"] = book_pnl_total(wagers, int(threshold))
    return res


def _accepted_numbers(wagers):
    agg = {}
    for w in wagers:
        k = (w["side"], float(w["accepted_line"]), float(w["accepted_price"]))
        a = agg.setdefault(k, [0, 0.0])
        a[0] += 1
        a[1] += w["stake"]
    return [{"side": s, "line": l, "price": p, "tickets": c, "handle": round(h, 2)}
            for (s, l, p), (c, h) in sorted(agg.items())]


def _by_archetype(wagers, hi_m, lo_m):
    groups = {}
    for w in wagers:
        groups.setdefault(w.get("archetype", "UNKNOWN"), []).append(w)
    out = {}
    for arch, ws in sorted(groups.items()):
        out[arch] = {
            "tickets": len(ws),
            "home_tickets": sum(1 for w in ws if w["side"] == "home"),
            "handle": round(math.fsum(w["stake"] for w in ws), 2),
            "home_handle": round(math.fsum(w["stake"] for w in ws if w["side"] == "home"), 2),
            "stake": _stake_stats([w["stake"] for w in ws]),
            "pnl_if_home_covers_all": book_pnl_total(ws, hi_m),
            "pnl_if_away_covers_all": book_pnl_total(ws, lo_m),
        }
    return out


def _concentration(wagers, handle):
    stakes = sorted((w["stake"] for w in wagers), reverse=True)
    by_bettor = {}
    for w in wagers:
        by_bettor[w.get("bettor_index")] = by_bettor.get(w.get("bettor_index"), 0.0) + w["stake"]
    top1 = stakes[: max(1, len(stakes) // 100)]
    hhi = math.fsum((v / handle) ** 2 for v in by_bettor.values()) if handle else None
    return {
        "top_1pct_tickets_handle_share": round(math.fsum(top1) / handle, 4) if handle else None,
        "top_10_wagers_handle_share": round(math.fsum(stakes[:10]) / handle, 4) if handle else None,
        "bettor_handle_hhi": round(hhi, 6) if hhi is not None else None,
        "distinct_bettors": len(by_bettor),
    }


# ----------------------------------------------------------------- m3: totals, moneyline, joint book

def total_result(side, line, total):
    x = (total - line) if side == "over" else (line - total)
    return "win" if x > 0 else ("push" if x == 0 else "loss")


def moneyline_result(side, home_margin):
    if home_margin == 0:
        return "push"          # US books grade a two-way NFL/CFB moneyline tie as no action
    won = home_margin > 0 if side == "home" else home_margin < 0
    return "win" if won else "loss"


def wager_outcome(w, home_score, away_score):
    m = w.get("market", "spread")
    margin = home_score - away_score
    if m == "spread":
        return wager_result(w["side"], w["accepted_line"], margin)
    if m == "total":
        return total_result(w["side"], w["accepted_line"], home_score + away_score)
    return moneyline_result(w["side"], margin)


def _pnl_of(result, stake, price):
    if result == "win":
        return -profit_on_win(stake, price)
    return float(stake) if result == "loss" else 0.0


def book_pnl_scores(wagers, home_score, away_score):
    return round(math.fsum(_pnl_of(wager_outcome(w, home_score, away_score), w["stake"], w["accepted_price"])
                           for w in wagers), 2)


def total_exposure(wagers):
    ws = [w for w in wagers if w.get("market") == "total"]
    over = [w for w in ws if w["side"] == "over"]
    oh, uh = math.fsum(w["stake"] for w in over), math.fsum(w["stake"] for w in ws if w["side"] == "under")
    out = {"tickets": len(ws), "over_tickets": len(over), "handle": round(oh + uh, 2),
           "over_handle": round(oh, 2), "under_handle": round(uh, 2)}
    if not ws:
        out.update(status="NO_WAGERS", financial_need_side=None)
        return out
    lines = sorted({float(w["accepted_line"]) for w in ws})
    grid = list(range(max(0, math.floor(lines[0]) - 1), math.ceil(lines[-1]) + 2))

    def pnl_at(t):
        return round(math.fsum(_pnl_of(total_result(w["side"], w["accepted_line"], t), w["stake"],
                                       w["accepted_price"]) for w in ws), 2)
    curve = [(t, pnl_at(t)) for t in grid]
    p_over, p_under = pnl_at(lines[-1] + CLEAN_MARGIN_PAD), pnl_at(-CLEAN_MARGIN_PAD)
    lo_clean, hi_clean = min(p_over, p_under), max(p_over, p_under)
    out.update({
        "status": "TIE" if p_over == p_under else "DIRECTIONAL",
        "financial_need_side": None if p_over == p_under else ("over" if p_over > p_under else "under"),
        "pnl_if_over_all": p_over, "pnl_if_under_all": p_under, "pnl_swing": round(abs(p_over - p_under), 2),
        "pnl_by_total": curve,
        "push_totals": [t for t in lines if float(t).is_integer()],
        "middle_risk_totals": [t for t, p in curve if p < lo_clean],
        "middle_benefit_totals": [t for t, p in curve if p > hi_clean],
    })
    return out


def moneyline_exposure(wagers):
    ws = [w for w in wagers if w.get("market") == "moneyline"]
    home = [w for w in ws if w["side"] == "home"]
    hh, ah = math.fsum(w["stake"] for w in home), math.fsum(w["stake"] for w in ws if w["side"] == "away")
    out = {"tickets": len(ws), "home_tickets": len(home), "handle": round(hh + ah, 2),
           "home_handle": round(hh, 2), "away_handle": round(ah, 2)}
    if not ws:
        out.update(status="NO_WAGERS", financial_need_side=None)
        return out

    def pnl(margin):
        return round(math.fsum(_pnl_of(moneyline_result(w["side"], margin), w["stake"], w["accepted_price"])
                               for w in ws), 2)
    ph, pa = pnl(1), pnl(-1)
    out.update({"status": "TIE" if ph == pa else "DIRECTIONAL",
                "financial_need_side": None if ph == pa else ("home" if ph > pa else "away"),
                "pnl_if_home_wins": ph, "pnl_if_away_wins": pa, "pnl_if_tie": pnl(0),
                "pnl_swing": round(abs(ph - pa), 2)})
    return out


def _grouped(wagers):
    g = {}
    for w in wagers:
        k = (w.get("market", "spread"), w["side"], float(w["accepted_line"]), float(w["accepted_price"]))
        g[k] = g.get(k, 0.0) + float(w["stake"])
    return [{"market": m, "side": sd, "accepted_line": l, "accepted_price": pr, "stake": st}
            for (m, sd, l, pr), st in g.items()]


def joint_book(wagers, max_score=70, ref_home_spread=None, ref_total=None):
    """Book P&L over every final score 0..max_score per team (all markets
    together). Exact for those scores; no score distribution is assumed.
    Optionally partitions scores by reference spread/total into the four
    'relevant outcome' regions and reports min/max P&L in each."""
    gw = _grouped(wagers)
    if not gw:
        return {"status": "NO_WAGERS"}
    best = worst = None
    regions = {}
    for h in range(max_score + 1):
        for a in range(max_score + 1):
            p = book_pnl_scores(gw, h, a)
            if best is None or p > best[0]:
                best = (p, h, a)
            if worst is None or p < worst[0]:
                worst = (p, h, a)
            if ref_home_spread is not None and ref_total is not None:
                sx = (h - a) + ref_home_spread
                tx = (h + a) - ref_total
                if sx == 0 or tx == 0:
                    continue
                key = ("home_covers" if sx > 0 else "away_covers") + "+" + ("over" if tx > 0 else "under")
                r = regions.setdefault(key, [p, p])
                r[0], r[1] = min(r[0], p), max(r[1], p)
    out = {"status": "OK", "best_case": {"pnl": best[0], "home_score": best[1], "away_score": best[2]},
           "worst_case": {"pnl": worst[0], "home_score": worst[1], "away_score": worst[2]},
           "score_range": [0, max_score]}
    if regions:
        out["regions"] = {k: {"min_pnl": v[0], "max_pnl": v[1]} for k, v in sorted(regions.items())}
        out["reference"] = {"home_spread": ref_home_spread, "total": ref_total}
    return out


def book_need_all(wagers, ref_home_spread=None, ref_total=None, max_score=70):
    """ArbitrEdge Need for every market plus the joint book. A Need is the
    outcome most favourable to the synthetic book's P&L: a liability fact,
    NOT a prediction or a betting recommendation."""
    return {"spread": exposure(wagers, ref_home_spread), "total": total_exposure(wagers),
            "moneyline": moneyline_exposure(wagers),
            "joint": joint_book(wagers, max_score, ref_home_spread, ref_total)}
