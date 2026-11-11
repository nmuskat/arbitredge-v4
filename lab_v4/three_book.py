"""
lab_v4/three_book.py -- the three-sportsbook "bet WITH the book" experiment (RESEARCH, exploratory).

  python -m lab_v4.three_book --real DIR --ovh DIR --market-pkl market.pkl --out DIR

Thresholds below were FIXED and committed before any result of this experiment was computed.
Decision time: kickoff - 1 h (wagers accepted by then). Bets graded at the best REAL executable price at that time
(lab market.pkl, built from the collected quote store), with closing value vs the consensus and market-maker close.
Independence: each configuration's bet on a game-market is ONE bet, its side = the majority of its 3 seeds' NEED
(qualifying in >= 2 seeds). Confidence intervals bootstrap whole GAMES (spread and total of a game together).
"""
import argparse
import collections
import json
import os
import pickle
import random
import statistics as st

from sportsbook_v4 import settlement as ST

TH = {"extreme": 0.65, "heavy": 0.60, "top_swing_quantile": 0.90, "horizon_s": 3600, "boot": 2000, "seed": 11,
      "min_bets_archetype": 1000, "worst_k": 5}
CONFIGS = {"CONTROL": ("real", ["ledger_A_s0.pkl", "ledger_A_all_s1.pkl", "ledger_A_all_s2.pkl"]),
           "A_PUBLIC": ("ovh", [f"ledger_TB_PUBLIC_s{s}.pkl" for s in (0, 1, 2)]),
           "B_EMOTION": ("ovh", [f"ledger_TB_EMOTION_s{s}.pkl" for s in (0, 1, 2)]),
           "C_PRICE": ("ovh", [f"ledger_TB_PRICE_s{s}.pkl" for s in (0, 1, 2)])}
ALT = ("A_PUBLIC", "B_EMOTION", "C_PRICE")
SIDES = {"spread": ("home", "away"), "total": ("over", "under")}
OPP = {"home": "away", "away": "home", "over": "under", "under": "over"}
CATS = ["1 all NEED", "2 rec tickets+handle agree, NEED against", "3 rec tickets >=65%, NEED against",
        "4 rec handle >=65%, NEED against", "5 tickets>=65% & handle>=65% & NEED all agree",
        "6 NEED survives removing top-5 wagers", "7 broad public NEED (survives excluding high-stakes)",
        "8 all three alt books agree", "9 >=2 of 3 alt books agree", "10 against heavily backed favourite (>=60%)",
        "11 against heavily backed over (>=60%)", "12 book's largest-liability decile",
        "C opposite of NEED (control)", "M market baseline (both sides)"]


def side_need(ws, m):
    if not ws:
        return None, 0.0, 0.0
    ex = ST.exposure(ws) if m == "spread" else ST.total_exposure(ws)
    a, b = (("pnl_if_home_covers_all", "pnl_if_away_covers_all") if m == "spread" else ("pnl_if_over_all", "pnl_if_under_all"))
    return ex.get("financial_need_side"), ex.get("pnl_swing") or 0.0, (ex.get(a) or 0.0) - (ex.get(b) or 0.0)


def game_markets(led):
    kick = {(s, g): k for s, g, k, *_ in led["games"]}
    by = collections.defaultdict(list)
    for wk in led["weeks"]:
        for w in wk["wagers"]:
            if w["market"] in ("spread", "total"):
                k = kick.get((wk["sport"], w["canonical_game_id"]))
                if k is not None and w["t"] <= k - TH["horizon_s"]:
                    by[(wk["sport"], w["canonical_game_id"], w["market"])].append(w)
    return by


def describe(ws, m):
    """Everything the experiment needs about one game-market's book at the decision time."""
    a, b = SIDES[m]
    rec = [w for w in ws if w["archetype"] != "SHARP"]
    hs = [w for w in ws if w["archetype"] == "HIGH_STAKES_REC"]

    def pct(xs, side, key):
        tot = sum(1 if key == "t" else w["stake"] for w in xs)
        return (sum(1 if key == "t" else w["stake"] for w in xs if w["side"] == side) / tot) if tot else None
    need, swing, signed = side_need(ws, m)
    if need is None:
        return None
    big = sorted(ws, key=lambda w: -w["stake"])
    rob = {k: side_need([w for w in ws if w not in big[:k]], m)[0] == need for k in (1, 3, 5)}
    _, _, s_hs = side_need(hs, m)
    rt, rh = pct(rec, a, "t"), pct(rec, a, "h")
    pub_t = a if (rt or 0) >= 0.5 else b
    pub_h = a if (rh or 0) >= 0.5 else b
    fav = None
    if m == "spread":
        hs_lines = [w["accepted_home_spread"] for w in ws if w.get("accepted_home_spread") is not None]
        if hs_lines:
            med = st.median(hs_lines)
            fav = "home" if med < 0 else "away" if med > 0 else None
    return {"need": need, "swing": swing, "signed": signed, "handle": sum(w["stake"] for w in ws),
            "rec_ticket_a": rt, "rec_handle_a": rh, "all_ticket_a": pct(ws, a, "t"), "all_handle_a": pct(ws, a, "h"),
            "pub_t": pub_t, "pub_t_pct": (rt if pub_t == a else 1 - rt) if rt is not None else None,
            "pub_h": pub_h, "pub_h_pct": (rh if pub_h == a else 1 - rh) if rh is not None else None,
            "hs_share_of_imbalance": (s_hs / signed) if signed else None,
            "robust1": rob[1], "robust3": rob[3], "robust5": rob[5],
            "survives_without_hs": side_need([w for w in ws if w["archetype"] != "HIGH_STAKES_REC"], m)[0] == need,
            "fav": fav, "n_wagers": len(ws)}


def qualifies(cat, d, m, swing_cut):
    n, pt, ph = d["need"], d["pub_t_pct"] or 0, d["pub_h_pct"] or 0
    if cat.startswith("1 "):
        return True
    if cat.startswith("2 "):
        return d["pub_t"] == d["pub_h"] and n != d["pub_t"]
    if cat.startswith("3 "):
        return pt >= TH["extreme"] and n != d["pub_t"]
    if cat.startswith("4 "):
        return ph >= TH["extreme"] and n != d["pub_h"]
    if cat.startswith("5 "):
        return pt >= TH["extreme"] and ph >= TH["extreme"] and d["pub_t"] == d["pub_h"] and n != d["pub_t"]
    if cat.startswith("6 "):
        return d["robust5"]
    if cat.startswith("7 "):
        return d["survives_without_hs"] and n != d["pub_t"]
    if cat.startswith("10 "):
        return m == "spread" and d["fav"] is not None and d["pub_t"] == d["fav"] and pt >= TH["heavy"] and n != d["fav"]
    if cat.startswith("11 "):
        return m == "total" and d["pub_t"] == "over" and pt >= TH["heavy"] and n == "under"
    if cat.startswith("12 "):
        return d["swing"] >= swing_cut
    return False


def consensus(per_seed):
    """per_seed: [ {key: side} ] -> {key: side} where >= 2 seeds give the same side."""
    out = {}
    for k in set().union(*per_seed):
        c = collections.Counter(s[k] for s in per_seed if k in s)
        side, n = c.most_common(1)[0]
        if n >= 2:
            out[k] = side
    return out


def outcome(mo, key, side):
    rec = mo.get(key)
    h = rec and rec["h"].get(1)
    return h and h.get("sides", {}).get(side)


def evaluate(bets, mo, B=None, seed=None):
    """bets: {key: side}. One bet per game-market; bootstrap by game."""
    B, rng = B or TH["boot"], random.Random(seed or TH["seed"])
    rows = []
    for key, side in bets.items():
        if side == "BOTH":
            os_ = [outcome(mo, key, s) for s in SIDES[key[2]]]
            if all(o and o.get("units") is not None for o in os_):
                rows.append((key, st.mean(o["units"] for o in os_), None, st.mean(o["clv_consensus_close"] for o in os_),
                             st.mean(o["clv_pro_close"] for o in os_)))
            continue
        o = outcome(mo, key, side)
        if o and o.get("units") is not None:
            rows.append((key, o["units"], o["result"], o.get("clv_consensus_close"), o.get("clv_pro_close")))
    if not rows:
        return {"n": 0}
    cells = {}
    for sp in ("nfl", "cfb"):
        for m in ("spread", "total"):
            rr = [r for r in rows if r[0][0] == sp and r[0][2] == m]
            cells[f"{sp} {m}"] = rec_roi(rr)
    games = collections.defaultdict(list)
    for r in rows:
        games[(r[0][0], r[0][1])].append(r[1])
    gk = list(games)
    tot = sum(sum(v) for v in games.values())
    nb = sum(len(v) for v in games.values())
    bs = []
    for _ in range(B):
        s = [gk[rng.randrange(len(gk))] for _ in gk]
        bs.append(sum(sum(games[g]) for g in s) / sum(len(games[g]) for g in s))
    bs.sort()
    clv = [r[3] for r in rows if r[3] is not None]
    clvp = [r[4] for r in rows if r[4] is not None]
    out = dict(rec_roi(rows), games=len(gk), roi_ci=[100 * bs[int(.025 * B)], 100 * bs[int(.975 * B) - 1]],
               p_roi_le0=(sum(1 for x in bs if x <= 0) + 1) / (B + 1), clv_consensus_pct=100 * st.mean(clv) if clv else None,
               clv_pro_pct=100 * st.mean(clvp) if clvp else None, cells=cells)
    out["roi_pct"] = 100 * tot / nb
    return out


def rec_roi(rr):
    w = sum(1 for r in rr if r[2] == "win")
    l = sum(1 for r in rr if r[2] == "loss")
    p = sum(1 for r in rr if r[2] == "push")
    return {"n": len(rr), "record": f"{w}-{l}-{p}", "roi_pct": 100 * st.mean(r[1] for r in rr) if rr else None}


def holm_bh(p, q=0.10):
    ks = sorted(p, key=p.get)
    m = len(ks)
    holm, stop = {}, False
    for i, k in enumerate(ks):
        stop = stop or p[k] > 0.05 / (m - i)
        holm[k] = not stop
    bh = {k: False for k in ks}
    for i in range(m, 0, -1):
        if p[ks[i - 1]] <= q * i / m:
            for k in ks[:i]:
                bh[k] = True
            break
    return holm, bh


def archetypes(leds):
    out = collections.defaultdict(lambda: collections.Counter())
    games = collections.defaultdict(set)
    for led in leds:
        for wk in led["weeks"]:
            for w in wk["wagers"]:
                if w["market"] not in ("spread", "total") or w.get("result") not in ("win", "loss", "push"):
                    continue
                c = out[w["archetype"]]
                c["n"] += 1
                c[w["result"]] += 1
                c["stake"] += w["stake"]
                c["pnl"] += w["customer_pnl"]
                c["nojuice"] += w["stake"] if w["result"] == "win" else -w["stake"] if w["result"] == "loss" else 0
                if w["market"] == "spread":
                    c["sp"] += 1
                    c["fav"] += (w["accepted_line"] or 0) < 0
                else:
                    c["tot"] += 1
                    c["over"] += w["side"] == "over"
                p = w.get("p_fair_decision")
                if p is not None:
                    c["ev_n"] += 1
                    c["ev_sum"] += p * (w["accepted_price"] / 100 if w["accepted_price"] > 0 else 100 / -w["accepted_price"]) - (1 - p)
                games[w["archetype"]].add((wk["sport"], w["canonical_game_id"]))
    res = {}
    for a, c in out.items():
        res[a] = {"bets": c["n"], "record": f"{c['win']}-{c['loss']}-{c['push']}", "games": len(games[a]),
                  "roi_with_vig": 100 * c["pnl"] / c["stake"], "roi_no_vig": 100 * c["nojuice"] / c["stake"],
                  "avg_wager": c["stake"] / c["n"], "handle_per_seed": c["stake"] / len(leds),
                  "fav_pct": 100 * c["fav"] / max(1, c["sp"]), "over_pct": 100 * c["over"] / max(1, c["tot"]),
                  "ev_at_decision_pct": 100 * c["ev_sum"] / max(1, c["ev_n"])}
    return res


def fade_bets(gms_by_seed, archetype):
    """Per seed: the archetype's net-handle side per game-market by T-1h -> fade (opposite); consensus over seeds."""
    per = []
    for gms in gms_by_seed:
        d = {}
        for key, ws in gms.items():
            a, b = SIDES[key[2]]
            h = sum(w["stake"] * (1 if w["side"] == a else -1) for w in ws if w["archetype"] == archetype)
            if h:
                d[key] = b if h > 0 else a
        per.append(d)
    return consensus(per)


def main(argv=None):
    ap = argparse.ArgumentParser()
    for k in ("real", "ovh", "market-pkl", "out"):
        ap.add_argument("--" + k, required=True)
    a = ap.parse_args(argv)
    mo = pickle.load(open(a.market_pkl, "rb"))
    os.makedirs(a.out, exist_ok=True)
    rep = {"thresholds": TH, "configs": {}, "strategies": {}, "fades": {}, "examples": []}
    desc, gms_all, finals, names = {}, {}, None, {}
    for cfg, (d, files) in CONFIGS.items():
        leds = [pickle.load(open(os.path.join(getattr(a, d), f), "rb")) for f in files]
        finals = finals or leds[0]["finals"]
        names.update({(s_, g): f"{aw} @ {h}" for s_, g, k, h, aw in leds[0]["games"]})
        gms = [game_markets(l) for l in leds]
        gms_all[cfg] = gms
        desc[cfg] = [{k: v for k, v in ((k, describe(ws, k[2])) for k, ws in g.items()) if v} for g in gms]
        rep["configs"][cfg] = {"archetypes": archetypes(leds),
                               "handle_spread_total_per_seed": [sum(w["stake"] for ws in g.values() for w in ws) for g in gms],
                               "tickets_per_seed": [sum(len(ws) for ws in g.values()) for g in gms]}
        ds = desc[cfg]
        keys = set.intersection(*[set(x) for x in ds])
        rep["configs"][cfg]["need"] = {
            "game_markets": len(keys),
            "same_side_3_seeds_pct": 100 * sum(1 for k in keys if len({x[k]["need"] for x in ds}) == 1) / len(keys),
            "robust1_pct": 100 * st.mean(x[k]["robust1"] for x in ds for k in x),
            "robust3_pct": 100 * st.mean(x[k]["robust3"] for x in ds for k in x),
            "robust5_pct": 100 * st.mean(x[k]["robust5"] for x in ds for k in x),
            "survives_without_hs_pct": 100 * st.mean(x[k]["survives_without_hs"] for x in ds for k in x),
            "hs_share_of_imbalance_median_pct": 100 * st.median(x[k]["hs_share_of_imbalance"] for x in ds for k in x
                                                                if x[k]["hs_share_of_imbalance"] is not None),
            "median_liability_swing": st.median(x[k]["swing"] for x in ds for k in x),
            "rec_ticket_majority_pct_median": 100 * st.median(x[k]["pub_t_pct"] for x in ds for k in x if x[k]["pub_t_pct"]),
            "need_against_rec_ticket_majority_pct": 100 * st.mean(x[k]["need"] != x[k]["pub_t"] for x in ds for k in x),
            "fav_share_rec_tickets_spreads_pct": 100 * st.mean(x[k]["pub_t"] == x[k]["fav"] for x in ds for k in x
                                                               if k[2] == "spread" and x[k]["fav"]),
            "over_share_rec_tickets_totals_pct": 100 * st.mean(x[k]["pub_t"] == "over" for x in ds for k in x if k[2] == "total")}
    # consensus NEED of each config
    cons = {cfg: consensus([{k: v["need"] for k, v in x.items()} for x in desc[cfg]]) for cfg in CONFIGS}
    pvals = {}
    for cfg in CONFIGS:
        ds = desc[cfg]
        cuts = []
        for x in ds:
            sw = collections.defaultdict(list)
            for k, v in x.items():
                sw[(k[0], k[2])].append(v["swing"])
            cuts.append({g: sorted(v)[int(TH["top_swing_quantile"] * (len(v) - 1))] for g, v in sw.items()})
        for cat in CATS:
            if cat.startswith(("8 ", "9 ")):
                continue
            if cat.startswith("C "):
                bets = {k: OPP[s] for k, s in cons[cfg].items()}
            elif cat.startswith("M "):
                bets = {k: "BOTH" for k in cons[cfg]}
            else:
                per = [{k: v["need"] for k, v in x.items() if qualifies(cat, v, k[2], cut[(k[0], k[2])])}
                       for x, cut in zip(ds, cuts)]
                bets = consensus(per)
                bets = {k: s for k, s in bets.items() if cons[cfg].get(k) == s}
            r = evaluate(bets, mo)
            r["per_seed"] = []
            if not cat.startswith(("C ", "M ")):
                for x, cut in zip(ds, cuts):
                    r["per_seed"].append(evaluate({k: v["need"] for k, v in x.items()
                                                   if qualifies(cat, v, k[2], cut[(k[0], k[2])])}, mo, B=200)
                                         .get("roi_pct"))
            rep["strategies"][f"{cfg} | {cat}"] = r
            if r.get("n") and not cat.startswith("M "):
                pvals[f"{cfg} | {cat}"] = r["p_roi_le0"]
    # cross-book consensus
    alls = set.intersection(*[set(cons[c]) for c in ALT])
    agree3 = {k: cons["A_PUBLIC"][k] for k in alls if len({cons[c][k] for c in ALT}) == 1}
    two = {}
    for k in set().union(*[set(cons[c]) for c in ALT]):
        c = collections.Counter(cons[x][k] for x in ALT if k in cons[x])
        s, n = c.most_common(1)[0]
        if n >= 2:
            two[k] = s
    for name, bets in (("ALT | 8 all three alt books agree", agree3), ("ALT | 9 >=2 of 3 alt books agree", two),
                       ("ALT | 8 + control also agrees", {k: s for k, s in agree3.items() if cons["CONTROL"].get(k) == s})):
        rep["strategies"][name] = evaluate(bets, mo)
        pvals[name] = rep["strategies"][name].get("p_roi_le0", 1.0)
    holm, bh = holm_bh(pvals)
    for k in pvals:
        rep["strategies"][k]["holm_pass"], rep["strategies"][k]["bh10_pass"] = holm[k], bh[k]
    rep["n_tests"] = len(pvals)
    # worst archetype x config and their fades
    allarch = [(cfg, ar, v) for cfg in CONFIGS for ar, v in rep["configs"][cfg]["archetypes"].items()
               if v["bets"] >= TH["min_bets_archetype"]]
    worst = sorted(allarch, key=lambda x: x[2]["roi_with_vig"])[:TH["worst_k"]]
    for cfg, ar, v in worst:
        rep["fades"][f"{cfg} | {ar}"] = {"bettor_result": v, "fade": evaluate(fade_bets(gms_all[cfg], ar), mo)}
    # examples: three-book consensus, largest control liability, 5 NFL + 5 CFB
    ex = []
    for sp in ("nfl", "cfb"):
        ks = sorted((k for k in agree3 if k[0] == sp and k in desc["CONTROL"][0]),
                    key=lambda k: -desc["CONTROL"][0][k]["swing"])[:5]
        for k in ks:
            dd = desc["A_PUBLIC"][0].get(k) or desc["CONTROL"][0][k]
            o = outcome(mo, k, agree3[k])
            ex.append({"key": k, "game": names.get((k[0], k[1])), "public_side": dd["pub_t"], "public_ticket_pct": dd["pub_t_pct"],
                       "public_handle_pct": dd["pub_h_pct"], "need": agree3[k], "control_swing": desc["CONTROL"][0][k]["swing"],
                       "final": finals.get((k[0], k[1])), "bet": o})
    rep["examples"] = ex
    json.dump(rep, open(os.path.join(a.out, "three_book.json"), "w"), indent=1, default=str)
    print("done", len(pvals), "tests")


if __name__ == "__main__":
    main()
