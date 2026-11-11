"""
lab_v4/overhaul_report.py -- behaviour and Financial NEED comparison for the bettor-behaviour overhaul.

  python -m lab_v4.overhaul_report --real DIR --ovh DIR --market-pkl market.pkl --out report.json

Configurations: A (production engine; ledgers from the realism audit: A_s0, A_all_s1, A_all_s2),
B2_20K / B2_10K / B2_5K (lab_v4.behavior_v2). Spreads and totals only for behaviour (moneyline reported apart).
Financial NEED = the side the synthetic book needs (sportsbook_v4.settlement exposure) from wagers placed by
kickoff - 1 h; graded at the best REAL executable price at that time (market.pkl), with closing value vs the
market makers' and the all-book consensus close. Nothing here changes any simulation.
"""
import argparse
import collections
import json
import os
import pickle
import random
import statistics as st

from lab_v4.concentration import need_side
from sportsbook_v4.market import profit_on_win

RUNS = {"A": ["ledger_A_s0.pkl", "ledger_A_all_s1.pkl", "ledger_A_all_s2.pkl"],
        "B2_20K": [f"ledger_B2_20K_s{s}.pkl" for s in (0, 1, 2)],
        "B2_10K": [f"ledger_B2_10K_s{s}.pkl" for s in (0, 1, 2)],
        "B2_5K": [f"ledger_B2_5K_s{s}.pkl" for s in (0, 1, 2)]}
SCALE = {"A": 1, "B2_20K": 1, "B2_10K": 2, "B2_5K": 4}       # handle normalisation to the 20k population


def wagers(led):
    for wk in led["weeks"]:
        for w in wk["wagers"]:
            yield wk, w


def ev_dec(w):
    p = w.get("p_fair_decision")
    if p is None:
        return None
    return p * profit_on_win(1.0, w["accepted_price"]) - (1 - p)


def behaviour(leds):
    arch = collections.defaultdict(lambda: collections.Counter())
    evs = collections.defaultdict(list)
    per_bettor = collections.defaultdict(lambda: [0.0, 0.0, 0])
    handle_by_bettor = collections.Counter()
    active_weeks = collections.Counter()
    for si, led in enumerate(leds):
        for wk, w in wagers(led):
            a = w["archetype"]
            if w["market"] == "moneyline":
                arch[a]["ml_n"] += 1
                continue
            r = w.get("result")
            if r not in ("win", "loss", "push"):
                continue
            c = arch[a]
            c["n"] += 1
            c[r] += 1
            c["stake"] += w["stake"]
            c["pnl"] += w["customer_pnl"]
            c["nojuice"] += w["stake"] if r == "win" else -w["stake"] if r == "loss" else 0
            if w["market"] == "spread":
                c["sp_n"] += 1
                c["fav"] += (w["accepted_line"] or 0) < 0
            else:
                c["tot_n"] += 1
                c["over"] += w["side"] == "over"
            e = ev_dec(w)
            if e is not None:
                evs[a].append(e)
            k = (si, w["bettor_uid"])
            pb = per_bettor[k]
            pb[0] += w["stake"]
            pb[1] += w["customer_pnl"]
            pb[2] += 1
            handle_by_bettor[k] += w["stake"]
            active_weeks[(si, w["bettor_uid"], wk["sport"], wk["window"][0])] += 1
    out = {}
    for a, c in sorted(arch.items()):
        if not c["n"]:
            continue
        out[a] = {"bets": c["n"], "record": f"{c['win']}-{c['loss']}-{c['push']}",
                  "win_pct": 100 * c["win"] / max(1, c["win"] + c["loss"]), "stake": c["stake"],
                  "roi_with_vig": 100 * c["pnl"] / c["stake"], "roi_no_juice": 100 * c["nojuice"] / c["stake"],
                  "ev_at_decision_pct": 100 * st.mean(evs[a]) if evs[a] else None,
                  "ev_sd_pct": 100 * st.pstdev(evs[a]) if len(evs[a]) > 1 else None,
                  "fav_share_spreads": 100 * c["fav"] / max(1, c["sp_n"]),
                  "over_share_totals": 100 * c["over"] / max(1, c["tot_n"]), "moneyline_bets": c["ml_n"]}
    rois = [100 * p / s for s, p, n in per_bettor.values() if n >= 10 and s > 0]
    tot = sum(handle_by_bettor.values())
    hb = sorted(handle_by_bettor.values(), reverse=True)
    n_b = len(hb)
    wk_counts = list(active_weeks.values())
    return {"archetypes": out,
            "bettor_roi_pct_n>=10": {"bettors": len(rois), "p10": pct(rois, 10), "p50": pct(rois, 50), "p90": pct(rois, 90)},
            "top1pct_bettors_handle_share": 100 * sum(hb[:max(1, n_b // 100)]) / tot if tot else None,
            "bets_per_active_bettor_week": st.mean(wk_counts) if wk_counts else None,
            "seeds": len(leds)}


def pct(x, q):
    if not x:
        return None
    x = sorted(x)
    return x[min(len(x) - 1, int(q / 100 * len(x)))]


def needs(led, scale=1.0):
    """{(sport, gid, market): dict(side, swing, handle, top1, top5, robust5)} at kickoff - 1 h."""
    kick = {(s, g): k for s, g, k, *_ in led["games"]}
    by = collections.defaultdict(list)
    for wk, w in wagers(led):
        if w["market"] in ("spread", "total"):
            by[(wk["sport"], w["canonical_game_id"], w["market"])].append(w)
    out = {}
    for (s, g, m), ws in by.items():
        k = kick.get((s, g))
        if k is None:
            continue
        sub = [w for w in ws if w["t"] <= k - 3600]
        side, swing = need_side(sub, m)
        if side is None:
            continue
        handle = sum(w["stake"] for w in sub)
        big = sorted(sub, key=lambda w: -w["stake"])
        r5, _ = need_side([w for w in sub if w not in big[:5]], m)
        r1, _ = need_side([w for w in sub if w is not big[0]], m)
        out[(s, g, m)] = {"side": side, "swing": swing * scale, "handle": handle * scale,
                          "top1": big[0]["stake"] / handle, "top5": sum(w["stake"] for w in big[:5]) / handle,
                          "robust1": r1 == side, "robust5": r5 == side,
                          "material": swing * scale >= 5000 or (handle and swing / handle >= 0.05)}
    return out


def need_eval(nd, mo, sel=lambda r: True, B=1000, seed=7):
    items = []
    for key, r in nd.items():
        if not sel(r):
            continue
        rec = mo.get(key)
        h = rec and rec["h"].get(1)
        o = h and h.get("sides", {}).get(r["side"])
        if not o:
            continue
        items.append((key, o))
    graded = [(k, o) for k, o in items if o.get("units") is not None]
    if not graded:
        return {"n": 0}
    w = sum(1 for _, o in graded if o["result"] == "win")
    l = sum(1 for _, o in graded if o["result"] == "loss")
    p = len(graded) - w - l
    units = [o["units"] for _, o in graded]
    clv = [o["clv_consensus_close"] for _, o in items if o.get("clv_consensus_close") is not None]
    clvp = [o["clv_pro_close"] for _, o in items if o.get("clv_pro_close") is not None]
    exp = [o["p_fair_close"] for _, o in graded if o["result"] != "push"]
    rng = random.Random(seed)
    bs = sorted(st.mean(rng.choice(units) for _ in units) for _ in range(B))
    return {"n": len(graded), "record": f"{w}-{l}-{p}", "roi_pct": 100 * st.mean(units),
            "roi_ci": [100 * bs[int(.025 * B)], 100 * bs[int(.975 * B) - 1]],
            "clv_consensus_pct": 100 * st.mean(clv) if clv else None, "clv_pro_pct": 100 * st.mean(clvp) if clvp else None,
            "wins_minus_fair_expected": w - sum(exp)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    for k in ("real", "ovh", "market-pkl", "out"):
        ap.add_argument("--" + k, required=True)
    a = ap.parse_args(argv)
    mo = pickle.load(open(a.market_pkl, "rb"))
    rep = {}
    for cfg, files in RUNS.items():
        d = a.real if cfg == "A" else a.ovh
        paths = [os.path.join(d, f) for f in files]
        if not all(os.path.exists(p) for p in paths):
            rep[cfg] = {"missing": [p for p in paths if not os.path.exists(p)]}
            continue
        leds = [pickle.load(open(p, "rb")) for p in paths]
        r = {"behaviour": behaviour(leds),
             "handle_natural": [sum(w["stake"] for _, w in wagers(l) if w["market"] in ("spread", "total")) for l in leds],
             "wagers": [sum(1 for _ in wagers(l)) for l in leds]}
        nds = [needs(l, SCALE[cfg]) for l in leds]
        keys = set.intersection(*[set(n) for n in nds])
        r["need"] = {"game_markets_with_need_all_seeds": len(keys),
                     "same_side_all_3_seeds_pct": 100 * sum(1 for k in keys if len({n[k]["side"] for n in nds}) == 1) / max(1, len(keys)),
                     "robust_remove_top1_pct": 100 * st.mean(n[k]["robust1"] for n in nds for k in n),
                     "robust_remove_top5_pct": 100 * st.mean(n[k]["robust5"] for n in nds for k in n),
                     "top1_wager_share_median_pct": 100 * st.median(n[k]["top1"] for n in nds for k in n),
                     "top5_wager_share_median_pct": 100 * st.median(n[k]["top5"] for n in nds for k in n),
                     "handle_per_game_market_median_normalized": st.median(n[k]["handle"] for n in nds for k in n)}
        ev = {}
        for si, n in enumerate(nds):
            for sp in ("nfl", "cfb"):
                for m in ("spread", "total"):
                    ev[f"seed{si} {sp} {m}"] = need_eval({k: v for k, v in n.items() if k[0] == sp and k[2] == m}, mo)
                    ev[f"seed{si} {sp} {m} material"] = need_eval({k: v for k, v in n.items() if k[0] == sp and k[2] == m},
                                                                  mo, lambda r: r["material"])
            ev[f"seed{si} all"] = need_eval(n, mo)
            ev[f"seed{si} all robust5"] = need_eval(n, mo, lambda r: r["robust5"])
        r["need_eval"] = ev
        rep[cfg] = r
        print(cfg, "done", flush=True)
    json.dump(rep, open(a.out, "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
