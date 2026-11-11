"""
lab_v4/bettor_report.py -- markdown tables for the bettor FADE/TAIL study (reads bettor_study.json + ledgers).

  python -m lab_v4.bettor_report --dir OUT --ledger-dir DIR --md report.md
"""
import argparse
import json
import os
import pickle

from lab_v4 import bettors as BT
from lab_v4.bettor_study import rules

NM = {"REC_CASUAL": "Casual rec", "HIGH_STAKES_REC": "High-stakes rec", "NARRATIVE_CHASER": "Narrative chaser",
      "TEAM_LOYAL": "Team loyal", "UNDERDOG_CONTRARIAN": "Underdog contrarian",
      "SELECTIVE_PRICE_SENSITIVE": "Selective", "SHARP": "Sharp"}


def P(x):
    return "-" if x is None else f"{100 * x:+.1f}%"


def define(rule):
    """Plain-English definition of a rule name."""
    top = rule.startswith("TOP")
    who = "highest" if top else "lowest"
    body = rule.split("_", 1)[1] if "_" in rule else rule
    if body == "ALL":
        scope = "all of their bets"
    elif body == "ALL" or rule.endswith("DECILE_ALL"):
        scope = "all of their bets"
    elif body in NM:
        scope = f"bets by {NM[body]} customers"
    else:
        m, sd = body.split("_")
        scope = f"{m} bets on the {sd}"
    size = "10% of bettors" if "DECILE" in rule else f"{''.join(c for c in rule.split('_')[0] if c.isdigit())} bettors"
    return (f"the {size} with the {who} evidence (excess-vs-price z) in {scope}, using only completed earlier weeks "
            f"(min 5 bets; 3 for bet-type groups); their NEXT week's bets in the same scope")


def training_snapshot(ledger_path):
    """In-sample (training) results of each group at the last cutoff, for contrast with out-of-sample."""
    led = pickle.load(open(ledger_path, "rb"))
    ws = BT.flatten(led)
    out = {}
    for sport in ("nfl", "cfb"):
        sw = [w for w in ws if w["sport"] == sport]
        last = max(w["week"] for w in sw)
        train = [w for w in sw if w["week"] < last]
        for name, rule in rules(15).items():
            mem = BT.select(train, rule.get("n_min", 5), rule, rule.get("k", 15))
            xs = [w for w in train if w["bettor_uid"] in mem and rule["scope"](w)]
            if xs:
                s = BT.summarize(xs)
                out[f"{name}|{sport}"] = {"bettors": len(mem), "wagers": s["wagers"], "record": s["record"],
                                          "roi": s["roi"], "excess": s["excess_units"], "z": s["excess_z"]}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--ledger-dir", required=True)
    ap.add_argument("--md", required=True)
    a = ap.parse_args(argv)
    R = json.load(open(os.path.join(a.dir, "bettor_study.json")))
    A = R["A_s0"]
    train = training_snapshot(os.path.join(a.ledger_dir, "ledger_A_s0.pkl"))
    L = []
    # ---- multiple testing over every (rule, sport, direction) out-of-sample test
    tests = {}
    for key, v in A["walk_forward"].items():
        for d in ("tail", "fade"):
            tests[f"{key}|{d}"] = v[d].get("p_one_sided")
    sig = BT.bh(tests, q=0.10)
    L.append(f"## Out-of-sample walk-forward results: production behaviour (seed 0)\n\n"
             f"{len(tests)} tests (30 group rules x 2 sports x tail/fade). Benjamini-Hochberg FDR 10%: "
             f"**{sum(sig.values())} significant**.\n")

    def row(key, d):
        v = A["walk_forward"][key]
        e = v[d]
        rule, sport = key.split("|")
        tr = train.get(key, {})
        s1 = R["A_all_s1"]["walk_forward"].get(key, {}).get(d, {})
        s2 = R["A_all_s2"]["walk_forward"].get(key, {}).get(d, {})
        stab = " / ".join("-" if x.get("excess_z") is None else f"{x['excess_z']:+.1f}" for x in (e, s1, s2))
        return (f"| {rule} | {sport} | {v['bettors']} | {e.get('games', 0)} | "
                f"{tr.get('record', '-')} {P(tr.get('roi'))} (z {tr.get('z')}) | {e.get('record', '-')} | {P(e.get('roi'))} "
                f"[{P(e['roi_ci'][0]) if e.get('roi_ci') else '-'}, {P(e['roi_ci'][1]) if e.get('roi_ci') else '-'}] | "
                f"{P(e.get('clv_at_close'))} | {stab} | {'-' if e.get('p_one_sided') is None else round(e['p_one_sided'], 3)} | "
                f"{'yes' if sig.get(f'{key}|{d}') else 'no'} |")
    hdr = ("| Group rule | Sport | Distinct bettors selected (all weeks) | Independent game-markets | Training (in-sample, last cutoff): record, ROI (z) | "
           "OUT-OF-SAMPLE record | OOS ROI at best executable price [95% CI] | OOS CLV vs close | OOS excess-vs-market z: seed 0 / 1 / 2 | "
           "one-sided p | Survives FDR? |\n|---|---|---|---|---|---|---|---|---|---|---|")
    for d, title in (("fade", "Top 10 candidate FADE groups (by out-of-sample evidence; fading = betting the OTHER side)"),
                     ("tail", "Top 10 candidate TAIL groups (by out-of-sample evidence; tailing = the SAME side)")):
        ranked = sorted((k for k in A["walk_forward"] if A["walk_forward"][k][d].get("excess_z") is not None),
                        key=lambda k: -A["walk_forward"][k][d]["excess_z"])
        L.append(f"\n### {title}\n")
        L.append(hdr)
        for k in ranked[:10]:
            L.append(row(k, d))
    L.append("\n### The literal question: best and worst bettors, followed or faded next week (all sports, seed 0)\n")
    L.append(hdr)
    for k in ("TOP15_ALL|nfl", "TOP15_ALL|cfb", "TOP_DECILE_ALL|nfl", "TOP_DECILE_ALL|cfb"):
        if k in A["walk_forward"]:
            L.append(row(k, "tail"))
    for k in ("BOTTOM15_ALL|nfl", "BOTTOM15_ALL|cfb", "BOTTOM_DECILE_ALL|nfl", "BOTTOM_DECILE_ALL|cfb"):
        if k in A["walk_forward"]:
            L.append(row(k, "fade"))
    # variants
    L.append("\n### Robustness: the same headline groups under other reasonable grouping methods (seed 0, out-of-sample)\n")
    L.append("| Variant | TOP15 tail NFL | TOP15 tail CFB | BOTTOM15 fade NFL | BOTTOM15 fade CFB | TOP decile tail (NFL / CFB) | BOTTOM decile fade (NFL / CFB) |\n|---|---|---|---|---|---|---|")
    def cellv(src, key, d):
        e = src.get(key, {}).get(d, {})
        return "-" if not e.get("n") else f"{e['record']} {P(e['roi'])} z {e['excess_z']:+.1f}" if e.get("excess_z") is not None else f"{e['record']} {P(e['roi'])}"
    srcs = [("ranked by evidence (z), k=15, from week 3 [primary]", A["walk_forward"])] + list(A.get("variants", {}).items())
    for name, src in srcs:
        L.append(f"| {name} | {cellv(src, 'TOP15_ALL|nfl', 'tail') if 'k=10' not in name else cellv(src, 'TOP10_ALL|nfl', 'tail')} | "
                 f"{cellv(src, 'TOP15_ALL|cfb', 'tail') if 'k=10' not in name else cellv(src, 'TOP10_ALL|cfb', 'tail')} | "
                 f"{cellv(src, 'BOTTOM15_ALL|nfl', 'fade') if 'k=10' not in name else cellv(src, 'BOTTOM10_ALL|nfl', 'fade')} | "
                 f"{cellv(src, 'BOTTOM15_ALL|cfb', 'fade') if 'k=10' not in name else cellv(src, 'BOTTOM10_ALL|cfb', 'fade')} | "
                 f"{cellv(src, 'TOP_DECILE_ALL|nfl', 'tail')} / {cellv(src, 'TOP_DECILE_ALL|cfb', 'tail')} | "
                 f"{cellv(src, 'BOTTOM_DECILE_ALL|nfl', 'fade')} / {cellv(src, 'BOTTOM_DECILE_ALL|cfb', 'fade')} |")
    # consensus
    L.append("\n### Group consensus (Phase 4): independent selected bettors agreeing on the same side (seed 0, out-of-sample)\n")
    L.append("| Situation | Action | Independent game-markets | Record | ROI at best executable price | CLV vs close | Excess z |\n|---|---|---|---|---|---|---|")
    for g, v in A["consensus"].items():
        d = "fade" if g.startswith("FADE") else "tail"
        e = v[d]
        if e.get("n"):
            L.append(f"| {g} | {d} | {e['games']} | {e['record']} | {P(e['roi'])} | {P(e.get('clv_at_close'))} | "
                     f"{'-' if e.get('excess_z') is None else round(e['excess_z'], 2)} |")
    # seeds summary: sign agreement of excess z across seeds for every test
    agree = tot = 0
    for key, v in A["walk_forward"].items():
        for d in ("tail", "fade"):
            zs = [R[n]["walk_forward"].get(key, {}).get(d, {}).get("excess_z") for n in ("A_s0", "A_all_s1", "A_all_s2")]
            if all(z is not None for z in zs):
                tot += 1
                agree += (all(z > 0 for z in zs) or all(z < 0 for z in zs))
    L.append(f"\n### Stability across random seeds\nOut-of-sample excess z has the same sign in all three seeds for "
             f"**{agree} of {tot}** tests (pure chance: 25%).\n")
    sig_s = {n: sum(BT.bh({f"{k}|{d}": R[n]["walk_forward"][k][d].get("p_one_sided") for k in R[n]["walk_forward"]
                                 for d in ("tail", "fade")}, q=0.10).values()) for n in ("A_all_s1", "A_all_s2", "R_s0")}
    L.append(f"FDR-significant tests in seed 1: {sig_s['A_all_s1']}, seed 2: {sig_s['A_all_s2']}; "
             f"corrected V4r1 model (separate, seed 0): {sig_s['R_s0']}.\n")
    # repeatability, baselines
    L.append("\n### Is there persistent bettor skill at all? (full season)\n")
    L.append(f"Empirical-Bayes between-bettor variance of excess return: tau^2 = {A['shrinkage']['tau2']:.4f} "
             f"(mean sampling variance {A['shrinkage']['mean_se2']:.3f}).\n")
    L.append("| Sport / archetype | Bettors | Correlation of excess, odd vs even weeks |\n|---|---|---|")
    for k, v in A["split_half"].items():
        L.append(f"| {k} | {v['bettors']} | {v['corr']:+.3f} |")
    L.append("\n### Side-type baselines in the evaluation weeks (every customer bet of that type, own price)\n")
    L.append("| Sport / market / side | Wagers | ROI | Excess vs decision fair |\n|---|---|---|---|")
    for k, v in A["side_type_baseline"].items():
        L.append(f"| {k} | {v['wagers']:,} | {P(v['roi'])} | {P(v['excess'])} |")
    md = "\n".join(L)
    open(a.md, "w").write(md)
    print(md)


if __name__ == "__main__":
    main()
