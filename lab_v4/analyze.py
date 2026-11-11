"""
lab_v4/analyze.py -- tables for the controlled NEED experiment (DEVELOPMENT data; nothing here is validation).

  python -m lab_v4.analyze --dir OUT --md report.md

NEED side graded at the BEST OBSERVED EXECUTABLE price at the decision horizon (real quotes), so every rule is judged
at prices that actually existed. Information test: actual wins minus the wins the professional close implied.
Baselines on the same game-markets: DOG/UNDER (market-only: the side getting points / the under), FAV/OVER, SHOP
(best EV vs professional fair at that time), BOTH (both sides). Bootstrap CIs over games.
"""
import argparse
import collections
import glob
import json
import math
import os
import pickle
import random

H = ("48", "24", "6", "1")


def load(d):
    runs = {}
    for p in sorted(glob.glob(os.path.join(d, "run_*.json"))):
        r = json.load(open(p))
        runs[(r["config"], r["seed"])] = r
    return runs, pickle.load(open(os.path.join(d, "market.pkl"), "rb"))


def side_out(mo, sport, gid, m, h, side):
    rec = mo.get((sport, gid, m))
    x = rec and rec["h"].get(int(h))
    return None if not x or not side else (x.get("sides") or {}).get(side)


def dog_side(mo, sport, gid, m, h):
    rec = mo.get((sport, gid, m))
    x = rec and rec["h"].get(int(h))
    if not x:
        return None
    if m == "total":
        return "under"
    v = x["view"]
    mu = v.get("consensus_mu")
    if mu is None or abs(mu) < 1e-9:
        return None
    return "away" if mu > 0 else "home"            # mu = expected home margin: positive -> home favourite


def shop_side(mo, sport, gid, m, h):
    rec = mo.get((sport, gid, m))
    x = rec and rec["h"].get(int(h))
    if not x or not x.get("sides") or not all(x["sides"].values()):
        return None
    return max(x["sides"], key=lambda s: x["sides"][s]["ev_now"])


def stats(items, boot=400):
    """items: [(game, outcome)]"""
    out = {"n": len(items)}
    if not items:
        return out
    dec = [(g, o) for g, o in items if o["result"] in ("win", "loss")]
    w = sum(o["result"] == "win" for _, o in dec)
    pu = sum(o["result"] == "push" for _, o in items)
    out["record"] = f"{w}-{len(dec) - w}-{pu}"

    def f_roi(xs):
        d = [o for _, o in xs if o["result"] in ("win", "loss")]
        return sum(o["units"] for o in d) / len(d) if d else None

    def f_mean(k):
        return lambda xs: (lambda v: sum(v) / len(v) if v else None)([o[k] for _, o in xs if o.get(k) is not None])
    fns = {"roi": f_roi, "clv_consensus": f_mean("clv_consensus_close"), "clv_pro": f_mean("clv_pro_close"),
           "retail_move": f_mean("retail_move_toward")}
    by = collections.defaultdict(list)
    for g, o in items:
        by[g].append((g, o))
    keys = sorted(by)
    rng = random.Random(11)
    bs = collections.defaultdict(list)
    for _ in range(boot):
        s = [x for k in (keys[rng.randrange(len(keys))] for _ in keys) for x in by[k]]
        for n_, f in fns.items():
            v = f(s)
            if v is not None:
                bs[n_].append(v)
    for n_, f in fns.items():
        v = f(items)
        b = sorted(bs[n_])
        out[n_] = None if v is None else round(v, 4)
        out[n_ + "_ci"] = [round(b[int(.025 * len(b))], 4), round(b[int(.975 * len(b)) - 1], 4)] if b else None
    exp = sum(o["p_fair_close"] for _, o in dec)
    var = sum(o["p_fair_close"] * (1 - o["p_fair_close"]) for _, o in dec)
    out["wins_vs_fair"] = round(w - exp, 1) if dec else None
    out["z"] = round((w - exp) / math.sqrt(var), 2) if var else None
    return out


def need_items(run, mo, h, pred=lambda n, x: True, which="side"):
    items = []
    for n in run["needs"]:
        x = n["h"].get(h)
        if not x or not x.get(which) or not pred(n, x):
            continue
        o = side_out(mo, n["sport"], n["gid"], n["market"], h, x[which])
        if o:
            items.append(((n["sport"], n["gid"]), o))
    return items


def baseline_items(mo, keys, h, rule):
    items = []
    for (sport, gid, m) in keys:
        if rule == "BOTH":
            for s in (("home", "away") if m == "spread" else ("over", "under")):
                o = side_out(mo, sport, gid, m, h, s)
                if o:
                    items.append(((sport, gid), o))
            continue
        s = {"DOG/UNDER": dog_side, "SHOP": shop_side}.get(rule, dog_side)(mo, sport, gid, m, h)
        if rule == "FAV/OVER" and s:
            s = {"home": "away", "away": "home", "under": "over"}[s]
        o = side_out(mo, sport, gid, m, h, s)
        if o:
            items.append(((sport, gid), o))
    return items


def fmt(name, s):
    P = lambda x: "-" if x is None else f"{100 * x:+.1f}%"           # noqa: E731
    C = lambda c: "" if not c else f" [{P(c[0])}, {P(c[1])}]"         # noqa: E731
    if not s.get("n"):
        return f"| {name} | 0 | | | | | | |"
    return (f"| {name} | {s['n']} | {s['record']} | {P(s.get('roi'))}{C(s.get('roi_ci'))} | {P(s.get('clv_consensus'))}"
            f"{C(s.get('clv_consensus_ci'))} | {P(s.get('clv_pro'))} | "
            f"{'-' if s.get('retail_move') is None else round(s['retail_move'], 3)} | {s.get('wins_vs_fair')} (z {s.get('z')}) |")


HDR = ("| Group | n | W-L-P | ROI at best observed price [95% CI] | CLV vs consensus close [CI] | CLV vs pro close | "
       "Retail move toward side (pts) | Wins vs fair expectation |\n|---|---|---|---|---|---|---|---|")


def report(d):
    runs, mo = load(d)
    L = []
    A = runs.get(("A", 0))
    configs = sorted({c for c, _ in runs})
    seeds = sorted({s for _, s in runs})
    # ------------------------------------------------------------------ Phase 1 / 4: hold, margin, archetypes
    L.append("## Book economics by configuration (mean over seeds; [min, max] across seeds)\n")
    L.append("| Config | Scope | Handle | Wagers | Nominal margin | Hold at closing fair | Realized hold |\n|---|---|---|---|---|---|---|")
    for c in configs:
        for scope in ("all|all", "nfl|spread", "nfl|total", "nfl|moneyline", "cfb|spread", "cfb|total", "cfb|moneyline"):
            xs = [runs[(c, s)]["hold"].get(scope) for s in seeds if (c, s) in runs]
            xs = [x for x in xs if x]
            if not xs:
                continue
            def mm(k, pct=True):
                v = [x[k] for x in xs if x.get(k) is not None]
                if not v:
                    return "-"
                f = (lambda y: f"{100 * y:.2f}%") if pct else (lambda y: f"{y:,.0f}")
                return f"{f(sum(v) / len(v))} [{f(min(v))}, {f(max(v))}]" if len(v) > 1 else f(v[0])
            L.append(f"| {c} | {scope} | {mm('handle', False)} | {mm('wagers', False)} | {mm('nominal_margin')} | "
                     f"{mm('hold_at_close_fair')} | {mm('realized_hold')} |")
    L.append("\n## Customer results by archetype (seed 0): ROI on settled stakes / CLV at the closing fair\n")
    archs = sorted({k for r in runs.values() for k in r["archetypes"]})
    L.append("| Archetype | " + " | ".join(configs) + " |\n|---|" + "---|" * len(configs))
    for a in archs:
        cells = []
        for c in configs:
            x = runs.get((c, 0), {}).get("archetypes", {}).get(a)
            cells.append("-" if not x or x["roi"] is None else
                         f"{100 * x['roi']:+.1f}% / {100 * (x['clv_at_close_fair'] or 0):+.1f}% (${x['handle'] / 1e6:.2f}M)")
        L.append(f"| {a} | " + " | ".join(cells) + " |")
    L.append("\n## Exposure management and passes (seed 0)\n")
    L.append("| Config | Wagers | Wagers while copying market makers (respected action) | Shade actions | Close actions | Reopen | price_not_attractive | limit_below_minimum | price_too_expensive |\n|---|---|---|---|---|---|---|---|---|")
    for c in configs:
        g = runs.get((c, 0), {}).get("management", {})
        L.append(f"| {c} | {g.get('wagers', 0):,} | {g.get('reference_mode_wagers', 0):,} ({100 * g.get('reference_mode_wagers', 0) / max(1, g.get('wagers', 1)):.0f}%) | "
                 f"{g.get('risk_SHADE', 0)} | {g.get('risk_CLOSE', 0)} | {g.get('risk_REOPEN', 0)} | {g.get('pass_price_not_attractive', 0):,} | "
                 f"{g.get('pass_limit_below_minimum', 0):,} | {g.get('pass_price_too_expensive', 0):,} |")
    # ------------------------------------------------------------------ NEED descriptives
    L.append("\n## Financial NEED: frequency, direction, dynamics (seed 0 unless stated)\n")
    L.append("| Config | Horizon | Game-markets | With NEED | Material | NEED = DOG/UNDER (market-only rule) | Spread NEED = dog | Total NEED = under | Median swing / handle |\n|---|---|---|---|---|---|---|---|---|")
    for c in configs:
        r = runs.get((c, 0))
        if not r:
            continue
        for h in H:
            xs = [(n, n["h"][h]) for n in r["needs"] if n["h"].get(h)]
            with_ = [(n, x) for n, x in xs if x["side"]]
            dog = [1 for n, x in with_ if dog_side(mo, n["sport"], n["gid"], n["market"], h) == x["side"]]
            sp = [(n, x) for n, x in with_ if n["market"] == "spread"]
            tt = [(n, x) for n, x in with_ if n["market"] == "total"]
            spd = sum(1 for n, x in sp if dog_side(mo, n["sport"], n["gid"], "spread", h) == x["side"])
            ratios = sorted(x["swing"] / x["handle"] for _, x in with_ if x["handle"])
            L.append(f"| {c} | T-{h}h | {len(xs)} | {len(with_)} | {sum(x['material'] for _, x in with_)} | "
                     f"{100 * len(dog) / max(1, len(with_)):.0f}% | {100 * spd / max(1, len(sp)):.0f}% | "
                     f"{100 * sum(1 for _, x in tt if x['side'] == 'under') / max(1, len(tt)):.0f}% | "
                     f"{ratios[len(ratios) // 2] if ratios else 0:.2f} |")
    L.append("\n### Persistence: share of game-markets whose NEED side at T-1 equals the side at earlier horizons (seed 0)\n")
    L.append("| Config | T-48 = T-1 | T-24 = T-1 | T-6 = T-1 | Any flip T-48..T-1 |\n|---|---|---|---|---|")
    for c in configs:
        r = runs.get((c, 0))
        if not r:
            continue
        rows = [n for n in r["needs"] if all(n["h"].get(h) and n["h"][h]["side"] for h in H)]
        same = {h: sum(n["h"][h]["side"] == n["h"]["1"]["side"] for n in rows) for h in ("48", "24", "6")}
        flip = sum(len({n["h"][h]["side"] for h in H}) > 1 for n in rows)
        L.append(f"| {c} | {100 * same['48'] / max(1, len(rows)):.0f}% | {100 * same['24'] / max(1, len(rows)):.0f}% | "
                 f"{100 * same['6'] / max(1, len(rows)):.0f}% | {100 * flip / max(1, len(rows)):.0f}% (n {len(rows)}) |")
    L.append("\n### Stability across random seeds: share of game-markets whose T-1 NEED side is identical in every seed\n")
    L.append("| Config | Seeds | All seeds agree | Material in seed 0 and all agree |\n|---|---|---|---|")
    for c in configs:
        ss = [runs[(c, s)] for s in seeds if (c, s) in runs]
        if len(ss) < 2:
            continue
        maps = [{(n["sport"], n["gid"], n["market"]): n["h"].get("1") for n in r["needs"]} for r in ss]
        keys = [k for k in maps[0] if all(m.get(k) and m[k]["side"] for m in maps)]
        agree = [k for k in keys if len({m[k]["side"] for m in maps}) == 1]
        mat = [k for k in keys if maps[0][k]["material"]]
        L.append(f"| {c} | {len(ss)} | {100 * len(agree) / max(1, len(keys)):.0f}% of {len(keys)} | "
                 f"{100 * sum(k in agree for k in mat) / max(1, len(mat)):.0f}% of {len(mat)} |")
    # ------------------------------------------------------------------ performance vs baselines
    keys = sorted({(n["sport"], n["gid"], n["market"]) for r in runs.values() for n in r["needs"]})
    for h in ("24", "1"):
        L.append(f"\n## NEED side at T-{h}h, graded at the best observed executable price at T-{h}h\n")
        L.append(HDR)
        for c in configs:
            for s in seeds:
                if (c, s) in runs:
                    L.append(fmt(f"{c} seed {s}: all NEED", stats(need_items(runs[(c, s)], mo, h))))
            if (c, 0) in runs:
                L.append(fmt(f"{c} seed 0: material NEED", stats(need_items(runs[(c, 0)], mo, h, lambda n, x: x["material"]))))
        for b in ("DOG/UNDER", "FAV/OVER", "SHOP", "BOTH"):
            L.append(fmt(f"BASELINE {b}", stats(baseline_items(mo, keys, h, b))))
    # incremental information: where NEED disagrees with the market-only rule
    L.append("\n## Incremental information: NEED where it DISAGREES with the market-only DOG/UNDER rule (T-1h)\n")
    L.append(HDR)
    for c in configs:
        for s in seeds:
            if (c, s) in runs:
                dis = lambda n, x: dog_side(mo, n["sport"], n["gid"], n["market"], "1") not in (None, x["side"])    # noqa: E731
                L.append(fmt(f"{c} seed {s}: NEED != DOG/UNDER", stats(need_items(runs[(c, s)], mo, "1", dis))))
    # by sport x market (A and E, seed 0)
    L.append("\n## NEED at T-1h by sport and market (seed 0)\n")
    L.append(HDR)
    for c in configs:
        r = runs.get((c, 0))
        if not r:
            continue
        for sp in ("nfl", "cfb"):
            for m in ("spread", "total"):
                L.append(fmt(f"{c} {sp} {m}", stats(need_items(r, mo, "1", lambda n, x, sp=sp, m=m: n["sport"] == sp and n["market"] == m))))
    # respected vs recreational drivers
    L.append("\n## NEED from recreational-only vs respected-only wagers (T-1h, seed 0)\n")
    L.append(HDR)
    for c in configs:
        r = runs.get((c, 0))
        if r:
            L.append(fmt(f"{c} recreational-only NEED", stats(need_items(r, mo, "1", which="rec_side"))))
            L.append(fmt(f"{c} respected-only NEED", stats(need_items(r, mo, "1", which="sharp_side"))))
    # lead/lag
    L.append("\n## Do NEED changes precede market movement? Change T-24h -> T-6h vs consensus move T-6h -> close (seed 0)\n")
    L.append("Direction agreement: NEED moving toward side A (book now gains more if A covers) vs the consensus fair moving toward A afterwards. "
             "Only game-markets with a consensus move of at least 0.25 pt.\n")
    L.append("| Config | Component | n | NEED change agrees with later move | 95% CI |\n|---|---|---|---|---|")
    for c in configs:
        r = runs.get((c, 0))
        if not r:
            continue
        for comp, k in (("all wagers", "signed"), ("recreational", "rec_signed"), ("respected", "sharp_signed")):
            n_ = a_ = 0
            for n in r["needs"]:
                x24, x6 = n["h"].get("24"), n["h"].get("6")
                rec = mo.get((n["sport"], n["gid"], n["market"]))
                v6 = rec and rec["h"].get(6)
                if not (x24 and x6 and v6 and rec.get("close")) or v6["view"].get("consensus_mu") is None:
                    continue
                dn = x6[k] - x24[k]
                dm = rec["close"]["consensus_mu"] - v6["view"]["consensus_mu"]
                if abs(dn) < 1 or abs(dm) < 0.25:
                    continue
                n_ += 1
                a_ += (dn > 0) == (dm > 0)
            p = a_ / n_ if n_ else 0
            se = math.sqrt(p * (1 - p) / n_) if n_ else 0
            L.append(f"| {c} | {comp} | {n_} | {100 * p:.1f}% | [{100 * (p - 1.96 * se):.1f}%, {100 * (p + 1.96 * se):.1f}%] |")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--md", required=True)
    a = ap.parse_args(argv)
    md = report(a.dir)
    open(a.md, "w").write(md)
    print(md[:3000])


if __name__ == "__main__":
    main()
