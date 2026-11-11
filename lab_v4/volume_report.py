"""
lab_v4/volume_report.py -- what changes when most recreational customers bet 3-4 games every game day.

  python -m lab_v4.volume_report --control DIR --vol DIR

Control = production engine ledgers (ledger_A_s0, A_all_s1, A_all_s2); VOL = same engine + game-day regulars
(ledger_VOL_s0..2). Per seed: bets per customer, the worst high-volume customers, group records, book hold.
"""
import argparse
import collections
import os
import pickle
import statistics as st

RUNS = {"CONTROL": ["ledger_A_s0.pkl", "ledger_A_all_s1.pkl", "ledger_A_all_s2.pkl"],
        "VOL": ["ledger_VOL_s0.pkl", "ledger_VOL_s1.pkl", "ledger_VOL_s2.pkl"],
        "VOL2": ["ledger_VOL2_s0.pkl", "ledger_VOL2_s1.pkl", "ledger_VOL2_s2.pkl"]}


def bettors(led):
    B = collections.defaultdict(lambda: {"w": 0, "l": 0, "p": 0, "n": 0, "st": 0.0, "pnl": 0.0, "nj": 0.0,
                                         "arch": None, "sport": None, "games": set(), "weeks": set(),
                                         "sports": collections.Counter()})
    for wk in led["weeks"]:
        for w in wk["wagers"]:
            b = B[w["bettor_uid"]]
            b["arch"] = w["archetype"]
            b["sports"][wk["sport"]] += 1
            b["sport"] = "+".join(sorted(b["sports"])) if len(b["sports"]) > 1 else wk["sport"]
            b["n"] += 1
            r = w["result"]
            b[{"win": "w", "loss": "l", "push": "p"}[r]] += 1
            b["st"] += w["stake"]
            b["pnl"] += w["customer_pnl"]
            b["nj"] += w["stake"] if r == "win" else -w["stake"] if r == "loss" else 0
            b["games"].add(w["canonical_game_id"])
            b["weeks"].add((wk["sport"], wk["window"][0]))
    return B


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", required=True)
    ap.add_argument("--vol", required=True)
    a = ap.parse_args(argv)
    for cfg, files in RUNS.items():
        d = a.control if cfg == "CONTROL" else a.vol
        print(f"==================== {cfg}")
        for si, f in enumerate(files):
            if not os.path.exists(os.path.join(d, f)):
                print("missing", f)
                break
            led = pickle.load(open(os.path.join(d, f), "rb"))
            B = bettors(led)
            ns = sorted(b["n"] for b in B.values())
            tot_st = sum(b["st"] for b in B.values())
            tot_p = sum(b["pnl"] for b in B.values())
            print(f"seed {si}: bettors {len(B)}, bets {sum(ns)}, median {st.median(ns)}, p90 {ns[int(.9 * len(ns))]}, "
                  f"max {ns[-1]}, >=60: {sum(1 for x in ns if x >= 60)}, >=100: {sum(1 for x in ns if x >= 100)}; "
                  f"handle ${tot_st:,.0f}, book +${-tot_p:,.0f} ({-100 * tot_p / tot_st:.1f}% hold)")
            if si:
                continue
            arch = collections.defaultdict(lambda: collections.Counter())
            for b in B.values():
                c = arch[(b["sport"], b["arch"])]
                for k in ("w", "l", "p", "n", "st", "pnl", "nj"):
                    c[k] += b[k]
                c["bettors"] += 1
            for (s, ar), c in sorted(arch.items()):
                print(f"   {s.upper()} {ar:26s} bettors {c['bettors']:5d} bets {c['n']:6d} per bettor {c['n'] / c['bettors']:5.1f} "
                      f"{c['w']}-{c['l']}-{c['p']} win {100 * c['w'] / (c['w'] + c['l']):.1f}% ROI {100 * c['pnl'] / c['st']:+.1f}% "
                      f"no-juice {100 * c['nj'] / c['st']:+.1f}%")
            big = [(u, b) for u, b in B.items() if b["n"] >= 60]
            print(f"   customers with 60+ bets: {len(big)}; two-sport persons: "
                  f"{sum(1 for b in B.values() if len(b['sports']) > 1)}")
            if big:
                wins = [b["w"] / (b["w"] + b["l"]) for _, b in big]
                print(f"   their win %: p5 {100 * sorted(wins)[int(.05 * len(wins))]:.1f}  median {100 * st.median(wins):.1f}  "
                      f"p95 {100 * sorted(wins)[int(.95 * len(wins))]:.1f}; ROI median "
                      f"{100 * st.median(b['pnl'] / b['st'] for _, b in big):+.1f}%; losing money: "
                      f"{sum(1 for _, b in big if b['pnl'] < 0)} of {len(big)}")
                print("   WORST 20 (60+ bets) by win %:")
                for u, b in sorted(big, key=lambda kv: (kv[1]["w"] / (kv[1]["w"] + kv[1]["l"]), kv[1]["pnl"]))[:20]:
                    print(f"   {u[:8]}|{b['sport'].upper()}|{b['arch']}|{b['n']}|{b['w']}-{b['l']}-{b['p']}|"
                          f"{100 * b['w'] / (b['w'] + b['l']):.1f}%|${b['st']:,.0f}|${b['pnl']:,.0f}|{100 * b['pnl'] / b['st']:+.0f}%|"
                          f"{len(b['weeks'])} wks|{len(b['games'])} games")
                print("   WORST 10 (60+ bets) by $:")
                for u, b in sorted(big, key=lambda kv: kv[1]["pnl"])[:10]:
                    print(f"   {u[:8]}|{b['sport'].upper()}|{b['arch']}|{b['n']}|{b['w']}-{b['l']}-{b['p']}|"
                          f"{100 * b['w'] / (b['w'] + b['l']):.1f}%|${b['st']:,.0f}|${b['pnl']:,.0f}|{100 * b['pnl'] / b['st']:+.0f}%")
                print("   BEST 5 (60+ bets) by win %:")
                for u, b in sorted(big, key=lambda kv: -kv[1]["w"] / (kv[1]["w"] + kv[1]["l"]))[:5]:
                    print(f"   {u[:8]}|{b['sport'].upper()}|{b['arch']}|{b['n']}|{b['w']}-{b['l']}-{b['p']}|"
                          f"{100 * b['w'] / (b['w'] + b['l']):.1f}%|${b['pnl']:,.0f}")


if __name__ == "__main__":
    main()
