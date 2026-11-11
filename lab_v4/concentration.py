"""
lab_v4/concentration.py -- archetype behaviour and high-stakes concentration from a wager-level ledger, and the
counterfactual effect of the largest wagers on Financial NEED (existing settlement code, every wager at its own
number and price).

Counterfactuals per game-market at T-1h and at kickoff (all pre-kickoff wagers):
  REMOVE k   the k largest wagers never happened;
  REPLACE k  the k largest wagers happen on the same side and number/price, at the game-market's median stake.
A reversal = the NEED side changes (a NEED that disappears -- tie/no wagers -- is counted separately).
"""
import collections
import statistics as st

from sportsbook_v4 import settlement as ST

NAME = {"REC_CASUAL": "Casual recreational", "HIGH_STAKES_REC": "High-stakes recreational", "NARRATIVE_CHASER": "Narrative chaser",
        "TEAM_LOYAL": "Team loyal", "UNDERDOG_CONTRARIAN": "Underdog contrarian",
        "SELECTIVE_PRICE_SENSITIVE": "Selective / price-sensitive", "SHARP": "Sharp"}


def need_side(ws, market):
    if not ws:
        return None, 0.0
    ex = ST.exposure(ws) if market == "spread" else ST.total_exposure(ws)
    return ex.get("financial_need_side"), ex.get("pnl_swing") or 0.0


def counterfactuals(led, kick, ks=(1, 5, 10), horizons=(3600, 0)):
    out = collections.defaultdict(lambda: collections.Counter())
    for wk in led["weeks"]:
        by = collections.defaultdict(list)
        for w in wk["wagers"]:
            if w["market"] in ("spread", "total"):
                by[(w["canonical_game_id"], w["market"])].append(w)
        for (gid, m), ws in by.items():
            for hz in horizons:
                t = kick[(wk["sport"], gid)] - hz
                sub = [w for w in ws if w["t"] <= t]
                side, _ = need_side(sub, m)
                if side is None:
                    continue
                c = out[(wk["sport"], m, hz)]
                c["need"] += 1
                med = st.median(w["stake"] for w in sub)
                big = sorted(sub, key=lambda w: -w["stake"])
                for k in ks:
                    top = {id(w) for w in big[:k]}
                    rem, _ = need_side([w for w in sub if id(w) not in top], m)
                    rep, _ = need_side([w if id(w) not in top else dict(w, stake=med) for w in sub], m)
                    c[f"remove{k}_reversed"] += rem is not None and rem != side
                    c[f"remove{k}_vanished"] += rem is None
                    c[f"replace{k}_reversed"] += rep is not None and rep != side
                top1 = big[0]
                c["top1_high_stakes"] += top1["archetype"] == "HIGH_STAKES_REC"
                c["top1_share_of_handle_x1000"] += round(1000 * top1["stake"] / sum(w["stake"] for w in sub))
    return {f"{s}|{m}|T-{h // 3600}h" if h else f"{s}|{m}|kickoff": dict(v) for (s, m, h), v in out.items()}


def archetypes(led, pop_counts):
    rows = collections.defaultdict(list)
    for wk in led["weeks"]:
        for w in wk["wagers"]:
            rows[w["archetype"]].append((wk["sport"], w))
    weeks = {(wk["sport"], wk["window"][0]) for wk in led["weeks"]}
    out = {}
    tot_handle = sum(w["stake"] for v in rows.values() for _, w in v)
    for a, xs in rows.items():
        ws = [w for _, w in xs]
        stakes = [w["stake"] for w in ws]
        cust = collections.Counter(w["bettor_uid"] for w in ws)
        handle = sum(stakes)
        spread = [w for w in ws if w["market"] == "spread"]
        ml = [w for w in ws if w["market"] == "moneyline"]
        tot = [w for w in ws if w["market"] == "total"]
        fav_sp = sum(1 for w in spread if w["accepted_line"] < 0)
        fav_ml = sum(1 for w in ml if w["accepted_price"] < 0)
        imp = lambda p: 100.0 / (p + 100.0) if p > 0 else -p / (-p + 100.0)          # noqa: E731
        ev = [(w["stake"], w["p_fair_decision"] * (w["accepted_price"] / 100 if w["accepted_price"] > 0 else 100 / -w["accepted_price"])
               - (1 - w["p_fair_decision"])) for w in ws if w.get("p_fair_decision") is not None]
        by_cust = sorted(collections.Counter({u: 0 for u in cust}).items())
        hs = collections.defaultdict(float)
        for w in ws:
            hs[w["bettor_uid"]] += w["stake"]
        top = sorted(hs.values(), reverse=True)
        n1 = max(1, len(top) // 100)
        out[a] = {"name": NAME[a], "population_customers": pop_counts.get(a), "customers_who_bet": len(cust),
                  "wagers": len(ws), "handle": round(handle, 2), "handle_share": round(handle / tot_handle, 4),
                  "mean_stake": round(st.mean(stakes), 2), "median_stake": st.median(stakes), "max_stake": max(stakes),
                  "wagers_per_customer": round(len(ws) / max(1, len(cust)), 1),
                  "wagers_per_customer_week": round(len(ws) / max(1, len(cust)) / max(1, len(weeks) / 2), 2),
                  "nfl_share_of_wagers": round(sum(1 for s, _ in xs if s == "nfl") / len(xs), 3),
                  "market_mix": {m: round(sum(1 for w in ws if w["market"] == m) / len(ws), 3) for m in ("spread", "total", "moneyline")},
                  "favourite_share_spread": round(fav_sp / max(1, len(spread)), 3),
                  "favourite_share_moneyline": round(fav_ml / max(1, len(ml)), 3),
                  "longshot_ml_share_of_ml_handle": round(sum(w["stake"] for w in ml if w["accepted_price"] >= 500) / max(1, sum(w["stake"] for w in ml)), 3),
                  "over_share_totals": round(sum(1 for w in tot if w["side"] == "over") / max(1, len(tot)), 3),
                  "mean_price_paid_implied": round(st.mean(imp(w["accepted_price"]) for w in ws), 4),
                  "roi": round(sum(w["customer_pnl"] for w in ws) / handle, 4),
                  "expected_roi_at_decision_fair": round(sum(s * e for s, e in ev) / sum(s for s, _ in ev), 4) if ev else None,
                  "stake_to_bankroll_median": round(st.median(w["stake"] / w["bankroll"] for w in ws if w.get("bankroll")), 4),
                  "top1pct_customers_share_of_archetype_handle": round(sum(top[:n1]) / handle, 3),
                  "largest_customer_handle": round(top[0], 2)}
    return out


def need_robustness(ws, market, k=5):
    """MEASUREMENT (no simulation change): the NEED side, and whether it survives removing the k largest wagers.
    Returns (side, robust) -- robust is False when the side changes or disappears without those wagers."""
    side, _ = need_side(ws, market)
    if side is None:
        return None, False
    big = {id(w) for w in sorted(ws, key=lambda w: -w["stake"])[:k]}
    rest, _ = need_side([w for w in ws if id(w) not in big], market)
    return side, rest == side
