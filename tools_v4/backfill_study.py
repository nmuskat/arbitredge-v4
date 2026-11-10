"""
tools_v4/backfill_study.py -- "where does the synthetic book make its money?" on the backfilled season.

Input: the JSON written by tools_v4/backfill_replay.py. MODELED customers, real odds, real final scores.

Split, fixed BEFORE looking at any result (docs_v4/BACKFILL.md):
  DISCOVERY  NFL weeks 1-4 (windows from 2026-09-08 to 2026-09-29), CFB weeks 0-4 (2026-08-25 to 2026-09-22)
  TEST       NFL weeks 5-7 (2026-10-06 to 2026-10-20),            CFB weeks 5-8 (2026-09-29 to 2026-10-20)

Every situation is a game-market (spread or total) described ONLY by information available at PICK time
(kickoff - 60 min): the sharp-vs-public card from wagers accepted by then and the consensus line then. Each
situation names a side to bet; it is graded at that pick-time consensus line against the real final score
(a push is a push). Money: risk 1.10 to win 1.00 (standard -110). Situations are a fixed list (SITUATIONS).
A rule "holds" only if it wins >= 55% with >= 25 graded bets in DISCOVERY and still beats break-even
(52.4%) in TEST.

  python -m tools_v4.backfill_study --replay replay.json --out study.json
"""

import argparse
import json
import math
from collections import defaultdict

DISCOVERY_END = {"nfl": "2026-10-06T12:00:00Z", "cfb": "2026-09-29T12:00:00Z"}
BREAK_EVEN = 110 / 210
MIN_DISCOVERY = 25


def period(sport, window_start):
    return "discovery" if window_start < DISCOVERY_END[sport] else "test"


def grade(market, side, home_line, total, final):
    """W/L/P for a side at the consensus line (home_line: the home team's spread)."""
    hs, as_ = final["home"], final["away"]
    if market == "spread":
        v = (hs - as_ + home_line) if side == "home" else (as_ - hs - home_line)
    else:
        v = (hs + as_ - total) if side == "over" else (total - hs - as_)
    return "W" if v > 0 else "L" if v < 0 else "P"


def other(market, side):
    return {"home": "away", "away": "home", "over": "under", "under": "over"}[side]


def _band(x, edges, labels):
    for e, lab in zip(edges, labels):
        if x < e:
            return lab
    return labels[-1]


def rows(replay):
    """One row per game-market with pick-time facts and the graded outcome of each side."""
    out = []
    for wk in replay["weeks"]:
        sport, ws = wk["sport"], wk["window"][0]
        for g in wk["games"]:
            if not g.get("final"):
                continue
            p = g["pick"]
            for m, key in (("spread", "home_spread"), ("total", "total")):
                line = p.get(key)
                card = (p.get("card") or {}).get(m) or {}
                if line is None or card.get("status") != "OK":
                    continue
                rec, sh = card["recreational"], card["sharp"]
                sides = ("home", "away") if m == "spread" else ("over", "under")
                rh = {s: rec["sides"][s]["handle"] for s in sides}
                shh = {s: sh["sides"][s]["handle"] for s in sides}
                pub = max(sides, key=lambda s: rh[s]) if sum(rh.values()) else None
                shp = max(sides, key=lambda s: shh[s]) if sum(shh.values()) and shh[sides[0]] != shh[sides[1]] else None
                res = {s: grade(m, s, line, line, g["final"]) for s in sides}
                fav = None
                if m == "spread" and line != 0:
                    fav = "home" if line < 0 else "away"
                out.append({
                    "sport": sport, "period": period(sport, ws), "window": ws, "event_id": g["event_id"],
                    "game": f"{g['away']} @ {g['home']}", "market": m, "line": line, "result": res,
                    "class": card.get("classification"), "public": pub,
                    "public_share": rh[pub] / sum(rh.values()) if pub else None,
                    "sharp": shp, "sharp_share": shh[shp] / sum(shh.values()) if shp else None,
                    "sharp_card_side": sh.get("majority_side"), "public_card_side": rec.get("majority_side"),
                    "fav": fav, "ranked": (g.get("rankings") or {}),
                    "close_line": (g.get("close") or {}).get(key)})
    return out


def _abs(x):
    return abs(x) if x is not None else None


# Fixed list: name -> (filter(row) -> bool, side(row) -> side or None)
SITUATIONS = {
    "sharp vs public: bet sharps": (lambda r: r["class"] == "SHARP_VS_PUBLIC", lambda r: r["sharp_card_side"]),
    "aligned: bet with both": (lambda r: r["class"] == "ALIGNED", lambda r: r["sharp_card_side"]),
    "fade the public (every game)": (lambda r: r["public"] is not None, lambda r: other(r["market"], r["public"])),
    "fade public 50-60%": (lambda r: r["public"] and r["public_share"] < .6, lambda r: other(r["market"], r["public"])),
    "fade public 60-70%": (lambda r: r["public"] and .6 <= r["public_share"] < .7, lambda r: other(r["market"], r["public"])),
    "fade public 70%+": (lambda r: r["public"] and r["public_share"] >= .7, lambda r: other(r["market"], r["public"])),
    "bigger sharp side (every game)": (lambda r: r["sharp"] is not None, lambda r: r["sharp"]),
    "sharp side 90%+ of sharp money": (lambda r: r["sharp"] and r["sharp_share"] >= .9, lambda r: r["sharp"]),
    "sharp side, sharps against public money": (lambda r: r["sharp"] and r["public"] and r["sharp"] != r["public"],
                                                lambda r: r["sharp"]),
    "public on favorite: fade (bet dog)": (lambda r: r["market"] == "spread" and r["fav"] and r["public"] == r["fav"],
                                           lambda r: other("spread", r["fav"])),
    "public on underdog: fade (bet fav)": (lambda r: r["market"] == "spread" and r["fav"] and r["public"] and r["public"] != r["fav"],
                                           lambda r: r["fav"]),
    "public on over: bet under": (lambda r: r["market"] == "total" and r["public"] == "over", lambda r: "under"),
    "public on under: bet over": (lambda r: r["market"] == "total" and r["public"] == "under", lambda r: "over"),
    "favorites (every spread)": (lambda r: r["market"] == "spread" and r["fav"], lambda r: r["fav"]),
    "underdogs (every spread)": (lambda r: r["market"] == "spread" and r["fav"], lambda r: other("spread", r["fav"])),
    "overs (every total)": (lambda r: r["market"] == "total", lambda r: "over"),
    "unders (every total)": (lambda r: r["market"] == "total", lambda r: "under"),
    "home teams (every spread)": (lambda r: r["market"] == "spread", lambda r: "home"),
    "road teams (every spread)": (lambda r: r["market"] == "spread", lambda r: "away"),
    "big favorites 7+: bet dog": (lambda r: r["market"] == "spread" and r["fav"] and abs(r["line"]) >= 7,
                                  lambda r: other("spread", r["fav"])),
    "small spreads <3.5: bet fav": (lambda r: r["market"] == "spread" and r["fav"] and abs(r["line"]) < 3.5,
                                    lambda r: r["fav"]),
}
SLICES = {"all": lambda r: True, "nfl": lambda r: r["sport"] == "nfl", "cfb": lambda r: r["sport"] == "cfb",
          "nfl spread": lambda r: r["sport"] == "nfl" and r["market"] == "spread",
          "nfl total": lambda r: r["sport"] == "nfl" and r["market"] == "total",
          "cfb spread": lambda r: r["sport"] == "cfb" and r["market"] == "spread",
          "cfb total": lambda r: r["sport"] == "cfb" and r["market"] == "total"}


def record(rs, side_fn):
    w = l_ = p = 0
    for r in rs:
        s = side_fn(r)
        if s is None:
            continue
        x = r["result"][s]
        w += x == "W"
        l_ += x == "L"
        p += x == "P"
    n = w + l_
    return {"w": w, "l": l_, "p": p, "pct": round(w / n, 3) if n else None,
            "units": round(w * 1.0 - l_ * 1.1, 2),
            "z": round((w - n * BREAK_EVEN) / math.sqrt(n * BREAK_EVEN * (1 - BREAK_EVEN)), 2) if n else None}


def study(replay):
    rs = rows(replay)
    out = {"label": replay.get("label"), "rows": len(rs), "situations": []}
    for name, (flt, side) in SITUATIONS.items():
        for sl, sf in SLICES.items():
            sub = [r for r in rs if flt(r) and sf(r)]
            d = record([r for r in sub if r["period"] == "discovery"], side)
            t = record([r for r in sub if r["period"] == "test"], side)
            if d["w"] + d["l"] + t["w"] + t["l"] == 0:
                continue
            holds = (d["w"] + d["l"] >= MIN_DISCOVERY and (d["pct"] or 0) >= 0.55
                     and t["w"] + t["l"] > 0 and (t["pct"] or 0) > BREAK_EVEN)
            out["situations"].append({"situation": name, "slice": sl, "discovery": d, "test": t,
                                      "all": record(sub, side), "holds": holds})
    return out, rs


def book_summary(replay):
    agg = defaultdict(lambda: {"handle": 0, "book": 0, "wagers": 0})
    arch = defaultdict(lambda: {"stake": 0, "pnl": 0, "w": 0, "l": 0})
    sport_of = {}
    for wk in replay["weeks"]:
        for g in wk["games"]:
            sport_of[g["event_id"]] = (wk["sport"], wk["window"][0])
    for s in replay["settlements"]:
        sp, ws = sport_of.get(s["event_id"], (s["sport"], "?"))
        for k in ((sp, ws), (sp, "season"), ("all", "season")):
            agg[k]["handle"] += s["stake_cents"] / 100
            agg[k]["book"] -= s["pnl_cents"] / 100
            agg[k]["wagers"] += 1
        a = arch[(sp, s["archetype"])]
        a["stake"] += s["stake_cents"] / 100
        a["pnl"] += s["pnl_cents"] / 100
        a["w"] += s["result"] == "win"
        a["l"] += s["result"] == "loss"
    return ({f"{k[0]}|{k[1]}": {**v, "hold_pct": round(100 * v["book"] / v["handle"], 2) if v["handle"] else None}
             for k, v in agg.items()},
            {f"{k[0]}|{k[1]}": {**v, "roi_pct": round(100 * v["pnl"] / v["stake"], 1) if v["stake"] else None}
             for k, v in arch.items()})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rep = json.load(open(a.replay))
    st, rs = study(rep)
    st["book"], st["archetypes"] = book_summary(rep)
    json.dump(st, open(a.out, "w"), indent=1)
    print(json.dumps({"rows": st["rows"], "holds": [(x["situation"], x["slice"]) for x in st["situations"] if x["holds"]]},
                     indent=1))


if __name__ == "__main__":
    main()
