"""
lab_v4/moneyline.py -- CFB/NFL moneyline audit: expected vs realized hold, de-vig sensitivity, game-level variance.

Fair win probability of each side comes from the market makers' (Pinnacle, else BetOnline group) last moneyline
quote before kickoff, de-vigged three ways: proportional (multiplicative), power (p_a^k + p_b^k = 1) and additive.
Game-level variance: for each game, book P&L if home wins / if away wins over ALL its moneyline wagers; expected
P&L = sum p * P&L; variance = p(1-p)(P&L_home - P&L_away)^2 (one Bernoulli per game: wagers on one game are not
independent). Realized - expected, divided by the SD over games, is the luck z-score.
"""
import math
import sqlite3

from lab_v4.reconcile import payout_profit


def imp(p):
    return 100.0 / (p + 100.0) if p > 0 else -p / (-p + 100.0)


def devig(ph, pa, method):
    a, b = imp(ph), imp(pa)
    if method == "proportional":
        return a / (a + b)
    if method == "additive":
        return min(0.999, max(0.001, a - (a + b - 1) / 2))
    lo, hi = 1.0, 5.0                       # power: find k with a^k + b^k = 1
    for _ in range(60):
        k = (lo + hi) / 2
        if a ** k + b ** k > 1:
            lo = k
        else:
            hi = k
    return a ** k


def close_quote(live, gid):
    rows = live.execute("SELECT book, side, price, fetched_at FROM mkt_quote WHERE event_id=? AND market='h2h' "
                        "AND fetched_at<commence ORDER BY fetched_at", (gid,)).fetchall()
    if not rows:
        return None
    last = {}
    for bk, sd, pr, fa in rows:
        last.setdefault(bk, {})[sd] = (pr, fa)
    for pref in ("pinnacle", "betonlineag", "lowvig"):
        q = last.get(pref)
        if q and "home" in q and "away" in q:
            return pref, q["home"][0], q["away"][0]
    return None


def audit(led, live, sport):
    games = {}
    for wk in led["weeks"]:
        if wk["sport"] != sport:
            continue
        for w in wk["wagers"]:
            if w["market"] == "moneyline":
                games.setdefault(w["canonical_game_id"], []).append(w)
    out = {"games": len(games), "wagers": sum(len(v) for v in games.values()),
           "handle": round(sum(w["stake"] for v in games.values() for w in v), 2)}
    realized = sum(-w["customer_pnl"] for v in games.values() for w in v)
    out["realized_book_pnl"] = round(realized, 2)
    for method in ("proportional", "power", "additive"):
        exp = var = 0.0
        n = 0
        for gid, ws in games.items():
            cq = close_quote(live, gid)
            if not cq:
                continue
            n += 1
            ph = devig(cq[1], cq[2], method)
            pnl_h = sum(-(payout_profit(w["stake"], w["accepted_price"]) if w["side"] == "home" else -w["stake"]) for w in ws)
            pnl_a = sum(-(payout_profit(w["stake"], w["accepted_price"]) if w["side"] == "away" else -w["stake"]) for w in ws)
            exp += ph * pnl_h + (1 - ph) * pnl_a
            var += ph * (1 - ph) * (pnl_h - pnl_a) ** 2
        out[method] = {"games_with_close": n, "expected_book_pnl": round(exp, 2),
                       "expected_hold": round(exp / out["handle"], 4), "sd_book_pnl": round(math.sqrt(var), 2),
                       "luck": round(realized - exp, 2), "z": round((realized - exp) / math.sqrt(var), 2) if var else None}
    # concentration: share of the realized result from the 10 biggest game swings
    sw = sorted(((abs(sum(-w["customer_pnl"] for w in ws)), gid) for gid, ws in games.items()), reverse=True)
    out["top10_games_share_of_abs_result"] = round(sum(x for x, _ in sw[:10]) / max(1e-9, sum(x for x, _ in sw)), 3)
    return out, games


def longshots(led, live, sport, min_price=1000):
    """Every game where a customer bet a moneyline at >= +min_price: fair prob, wagers, result."""
    rows = {}
    for wk in led["weeks"]:
        if wk["sport"] != sport:
            continue
        for w in wk["wagers"]:
            if w["market"] == "moneyline" and w["accepted_price"] >= min_price:
                r = rows.setdefault(w["canonical_game_id"], {"wagers": 0, "handle": 0.0, "won": w["result"] == "win",
                                                             "max_price": 0, "side": w["side"]})
                r["wagers"] += 1
                r["handle"] += w["stake"]
                r["max_price"] = max(r["max_price"], w["accepted_price"])
    for gid, r in rows.items():
        cq = close_quote(live, gid)
        p = None
        if cq:
            ph = {m: devig(cq[1], cq[2], m) for m in ("proportional", "power")}
            p = {m: (v if r["side"] == "home" else 1 - v) for m, v in ph.items()}
        r["fair_close"] = p
    return rows
