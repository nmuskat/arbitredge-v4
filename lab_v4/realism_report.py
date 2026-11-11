"""
lab_v4/realism_report.py -- before/after tables for the V4 realism audit (DEVELOPMENT data).

  python -m lab_v4.realism_report --dir DIR --market-db market.db --md report.md

Realism and accounting only: no table ranks configurations by betting results.
"""
import argparse
import collections
import glob
import math
import os
import pickle
import sqlite3
import statistics as st

from lab_v4 import moneyline as ML
from lab_v4 import reconcile as REC
from lab_v4.concentration import need_side
from lab_v4.reconcile import payout_profit
from need_v6.market import normalizer

MATERIAL_ABS, MATERIAL_SHARE = 5000.0, 0.05


def wagers(led, sport=None):
    for wk in led["weeks"]:
        if sport is None or wk["sport"] == sport:
            for w in wk["wagers"]:
                yield wk["sport"], w


def ev_dec(w):
    p = w.get("p_fair_decision")
    return None if p is None else p * payout_profit(1, w["accepted_price"]) - (1 - p)


def economics(led):
    out = {}
    for scope in ("all", "nfl", "cfb"):
        ws = [w for s, w in wagers(led) if scope in ("all", s)]
        h = sum(w["stake"] for w in ws)
        pnl = -sum(w["customer_pnl"] for w in ws)
        e = [(w["stake"], ev_dec(w)) for w in ws if ev_dec(w) is not None]
        out[scope] = {"wagers": len(ws), "handle": h, "book_pnl": pnl, "realized_hold": pnl / h,
                      "expected_hold": -sum(s * x for s, x in e) / sum(s for s, _ in e)}
    return out


def sharps(led, mo):
    out = {}
    for sp in ("nfl", "cfb"):
        sh = [w for s, w in wagers(led, sp) if w["respected"]]
        evs = [ev_dec(w) for w in sh if ev_dec(w) is not None]
        cl = []
        for w in sh:
            if w["market"] == "moneyline":
                continue
            rec = mo.get((sp, w["canonical_game_id"], w["market"]))
            if rec and rec.get("close"):
                pw, pp = normalizer(sp, w["market"]).prob_at(rec["close"]["pro_mu"], w["side"], w["accepted_line"])
                cl.append(pw * payout_profit(1, w["accepted_price"]) - (1 - pw - pp))
        h = sum(w["stake"] for w in sh)
        out[sp] = {"wagers": len(sh), "handle": h, "ev_decision": st.mean(evs) if evs else None,
                   "share_ev_pos": sum(e > 0 for e in evs) / len(evs) if evs else None,
                   "clv_close": st.mean(cl) if cl else None,
                   "roi": sum(w["customer_pnl"] for w in sh) / h if h else None}
    return out


def need_table(led, kick, hz=3600):
    out = {}
    for wk in led["weeks"]:
        by = collections.defaultdict(list)
        for w in wk["wagers"]:
            if w["market"] in ("spread", "total"):
                by[(w["canonical_game_id"], w["market"])].append(w)
        for (gid, m), ws in by.items():
            t = kick[(wk["sport"], gid)] - hz
            sub = [w for w in ws if w["t"] <= t]
            side, swing = need_side(sub, m)
            ex = sub and (sum(w["stake"] for w in sub))
            signed = 0.0
            if sub:
                from sportsbook_v4 import settlement as ST
                e = ST.exposure(sub) if m == "spread" else ST.total_exposure(sub)
                a = e.get("pnl_if_home_covers_all" if m == "spread" else "pnl_if_over_all") or 0.0
                b = e.get("pnl_if_away_covers_all" if m == "spread" else "pnl_if_under_all") or 0.0
                signed = a - b
            big = max(sub, key=lambda w: w["stake"]) if sub else None
            out[(wk["sport"], gid, m)] = {"side": side, "swing": swing, "signed": signed,
                                          "material": bool(side) and swing >= max(MATERIAL_ABS, MATERIAL_SHARE * (ex or 0)),
                                          "top1_share": (big["stake"] / ex) if big else None}
    return out


def stability(tables):
    """tables: [need_table] of matched runs -> agreement statistics."""
    keys = [k for k in tables[0] if all(t.get(k) and t[k]["side"] for t in tables)]
    agree = [k for k in keys if len({t[k]["side"] for t in tables}) == 1]
    mat = [k for k in keys if tables[0][k]["material"]]
    pairs = [(a, b) for i, a in enumerate(tables) for b in tables[i + 1:]]
    pair_rev = st.mean(sum(a[k]["side"] != b[k]["side"] for k in keys) / len(keys) for a, b in pairs) if pairs else None
    sw = [st.pstdev([t[k]["signed"] for t in tables]) / max(1.0, abs(st.mean([t[k]["signed"] for t in tables]))) for k in keys]
    return {"game_markets": len(keys), "all_agree": len(agree) / len(keys),
            "material_all_agree": sum(k in agree for k in mat) / max(1, len(mat)),
            "pairwise_reversal": pair_rev, "material_freq": st.mean(sum(t[k]["material"] for k in keys) / len(keys) for t in tables),
            "median_signed_cv": st.median(sw)}


def moneyline_block(led, live):
    out = {}
    for sp in ("cfb", "nfl"):
        a, games = ML.audit(led, live, sp)
        ws = [w for s, w in wagers(led, sp) if w["market"] == "moneyline"]
        h = sum(w["stake"] for w in ws)
        out[sp] = {"wagers": len(ws), "handle": h, "realized_hold": a["realized_book_pnl"] / h,
                   "expected_hold_power": a["power"]["expected_hold"], "expected_hold_proportional": a["proportional"]["expected_hold"],
                   "sd_hold_power": a["power"]["sd_book_pnl"] / h, "z_power": a["power"]["z"],
                   "longshot500_handle_share": sum(w["stake"] for w in ws if w["accepted_price"] >= 500) / h,
                   "max_potential_win": max(payout_profit(w["stake"], w["accepted_price"]) for w in ws)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--market-db", required=True)
    ap.add_argument("--market-pkl", required=True)
    ap.add_argument("--md", required=True)
    a = ap.parse_args(argv)
    live = sqlite3.connect(a.market_db)
    mo = pickle.load(open(a.market_pkl, "rb"))
    led = {os.path.basename(p)[7:-4]: pickle.load(open(p, "rb")) for p in sorted(glob.glob(os.path.join(a.dir, "ledger_*.pkl")))
           if os.path.basename(p) != "ledger_A0.pkl"}
    kick = {(s, e): k for s, e, k, _, _ in next(iter(led.values()))["games"]}
    L = []
    P = lambda x: "-" if x is None else f"{100 * x:+.2f}%"           # noqa: E731
    # reconciliation of every ledger
    L.append("## Accounting reconciliation (independent re-settlement of every wager)\n")
    L.append("| Ledger | Wagers | Unsettled | Result mismatches | P&L mismatches |\n|---|---|---|---|---|")
    for n, x in led.items():
        r = REC.replay(x)
        L.append(f"| {n} | {r['wagers']:,} | {r['unsettled']} | {r['mismatch_result']} | {r['mismatch_pnl']} |")
    # economics
    L.append("\n## Book economics: expected hold (at decision-time market-maker fair) vs realized\n")
    L.append("| Ledger | Scope | Wagers | Handle | Book P&L | Expected hold | Realized hold |\n|---|---|---|---|---|---|---|")
    for n in ("A_s0", "A_all_s1", "A_all_s2", "RSHARP_s0", "RMAXWIN_s0", "R_s0", "R_s1", "R_s2"):
        if n not in led:
            continue
        e = economics(led[n])
        for sc in ("nfl", "cfb", "all"):
            x = e[sc]
            L.append(f"| {n} | {sc} | {x['wagers']:,} | ${x['handle']:,.0f} | ${x['book_pnl']:,.0f} | {P(x['expected_hold'])} | {P(x['realized_hold'])} |")
    # sharps
    L.append("\n## Sharp customers: decision quality (no later information)\n")
    L.append("| Ledger | Sport | Wagers | Handle | Mean EV at decision vs maker no-vig fair | Share +EV | CLV at professional close (spread/total) | ROI (outcome) |\n|---|---|---|---|---|---|---|---|")
    for n in ("A_s0", "RSHARP_s0", "R_s0", "R_s1", "R_s2"):
        if n in led:
            for sp, x in sharps(led[n], mo).items():
                L.append(f"| {n} | {sp} | {x['wagers']:,} | ${x['handle']:,.0f} | {P(x['ev_decision'])} | "
                         f"{P(x['share_ev_pos']) if x['share_ev_pos'] is None else str(round(100 * x['share_ev_pos'])) + '%'} | "
                         f"{P(x['clv_close'])} | {P(x['roi'])} |")
    # moneyline
    L.append("\n## Moneyline: expected vs realized hold (game-level variance; power de-vig)\n")
    L.append("| Ledger | Sport | Wagers | Handle | Long-shot (+500 or longer) handle share | Largest potential win | Expected hold (power) | Expected hold (proportional) | Realized hold | SD of hold | z |\n|---|---|---|---|---|---|---|---|---|---|---|")
    for n in ("A_s0", "RMAXWIN_s0", "R_s0", "R_s1", "R_s2"):
        if n in led:
            for sp, x in moneyline_block(led[n], live).items():
                L.append(f"| {n} | {sp} | {x['wagers']:,} | ${x['handle']:,.0f} | {100 * x['longshot500_handle_share']:.1f}% | "
                         f"${x['max_potential_win']:,.0f} | {P(x['expected_hold_power'])} | {P(x['expected_hold_proportional'])} | "
                         f"{P(x['realized_hold'])} | {100 * x['sd_hold_power']:.1f}% | {x['z_power']} |")
    # NEED + stability
    tabs = {n: need_table(x, kick) for n, x in led.items()}
    L.append("\n## Financial NEED at T-1h: concentration and materiality\n")
    L.append("| Ledger | Game-markets with NEED | Material | Median share of handle in the single largest wager |\n|---|---|---|---|")
    for n in ("A_s0", "RSHARP_s0", "RMAXWIN_s0", "R_s0"):
        if n in tabs:
            t = [v for v in tabs[n].values() if v["side"]]
            L.append(f"| {n} | {len(t)} | {sum(v['material'] for v in t)} | {100 * st.median(v['top1_share'] for v in t):.1f}% |")
    L.append("\n## NEED stability: which randomness moves the T-1h NEED side? (base = production seed 0; others re-keyed)\n")
    L.append("| Experiment (what is re-randomised) | Runs | Same side in all runs | Material NEED same side in all runs | Mean pairwise reversal | Material frequency | Median CV of signed liability |\n|---|---|---|---|---|---|---|")
    exps = [("everyone (arrivals, choices, stakes)", ["A_s0", "A_all_s1", "A_all_s2"]),
            ("high-stakes recreational only", ["A_s0", "A_hs_s1", "A_hs_s2"]),
            ("other recreational only", ["A_s0", "A_otherrec_s1", "A_otherrec_s2"]),
            ("sharps only", ["A_s0", "A_sharp_s1", "A_sharp_s2"]),
            ("V4r1 corrected, everyone", ["R_s0", "R_s1", "R_s2"])]
    for name, ns in exps:
        if all(n in tabs for n in ns):
            s = stability([tabs[n] for n in ns])
            L.append(f"| {name} | {len(ns)} | {100 * s['all_agree']:.0f}% | {100 * s['material_all_agree']:.0f}% | "
                     f"{100 * s['pairwise_reversal']:.0f}% | {100 * s['material_freq']:.0f}% | {s['median_signed_cv']:.2f} |")
    if "A_s0" in tabs and "R_s0" in tabs:
        k = [x for x in tabs["A_s0"] if tabs["A_s0"][x]["side"] and tabs["R_s0"].get(x) and tabs["R_s0"][x]["side"]]
        diff = [x for x in k if tabs["A_s0"][x]["side"] != tabs["R_s0"][x]["side"]]
        mat = [x for x in diff if tabs["A_s0"][x]["material"] or tabs["R_s0"][x]["material"]]
        L.append(f"\nV4 (seed 0) vs V4r1 (seed 0, common random numbers): T-1h NEED side differs in {len(diff)} of {len(k)} "
                 f"game-markets ({100 * len(diff) / len(k):.0f}%); {len(mat)} of those are material in either.")
    md = "\n".join(L)
    open(a.md, "w").write(md)
    print(md)


if __name__ == "__main__":
    main()
