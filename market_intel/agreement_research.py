"""
market_intel/agreement_research.py -- the 2026 MARKET NEED x SYNTHETIC SHARP AGREEMENT research results, frozen
for display in the Market Intelligence tab (owner request 2026-11-12: "show the agreement results with complete
records").

Source: original_v3/agreement.py (branch research/original-intel, commit 9bb08c6), run 2026-11-12 on every
completed 2026 game in the collected data. EXPLORATORY and RETROSPECTIVE: the NEED side is a replay of the original
engine on already-collected quotes, not a frozen pre-game record. Two sharp-position sources, never mixed:
  genuine        positions from the production synthetic-book ledger files PUBLISHED before the cutoff
  reconstructed  the full-season replay of the same engine (positions re-simulated after the fact)
Bets graded at the best real price at the cutoff (original_v3.evaluate.Grader), 1 unit risked per bet.
SIMULATED sharps: synthetic-book model output, never real sportsbook money. Not official plays; default NO BET.

  python -m market_intel.agreement_research --agree agree.pkl --replays full_v3.pkl full_v4.pkl mi_v3.pkl mi_v4.pkl --out market_intel/agreement_2026.json.gz
"""
import argparse
import collections
import gzip
import json
import os
import time

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agreement_2026.json.gz")
NEED_CATS = ("STRONG_AGREEMENT", "MODERATE_AGREEMENT", "DISAGREEMENT", "NO_SHARP_CONSENSUS", "MARKET_NEED_ONLY")
ORDER = NEED_CATS + ("SHARP_ONLY", "NO_SIGNAL")
SOURCES = ("genuine", "recon")


def _bet(b):
    if not b:
        return None
    return {"res": b["res"], "units": round(b["units"], 4), "clv": None if b.get("clv") is None else round(b["clv"], 4)}


def _tally(bets):
    bets = [b for b in bets if b]
    w = sum(b["res"] == "W" for b in bets)
    lo = sum(b["res"] == "L" for b in bets)
    p = sum(b["res"] == "P" for b in bets)
    u = sum(b["units"] for b in bets)
    clv = [b["clv"] for b in bets if b.get("clv") is not None]
    return {"n": len(bets), "W": w, "L": lo, "P": p, "units": round(u, 2),
            "roi": round(u / len(bets), 4) if bets else None,
            "win_pct": round(w / (w + lo), 4) if w + lo else None,
            "clv": round(sum(clv) / len(clv), 4) if clv else None}


def export(rows, teams, finals=None):
    finals = finals or {}
    games = []
    for r in rows:
        src = {}
        for s in SOURCES:
            x = r.get(s) or {}
            if not x.get("available"):
                src[s] = None
                continue
            pos = x.get("pos") or {}
            src[s] = {"cat": x["cat"], "why": x["why"], "dir": pos.get("direction"), "str": pos.get("strength"),
                      "status": pos.get("status"), "tickets": pos.get("tickets"), "bettors": pos.get("bettors"),
                      "sharp_bet": _bet(x.get("sharp_bet"))}
        if r["need"] is None and not any(v and v["dir"] for v in src.values()):
            continue                                   # nothing to show: no NEED and no directional sharp side
        k, home, away = teams.get(r["eid"], (None, "?", "?"))
        games.append({"rule": r["cad"], "cut": r["label"], "sport": r["sport"], "market": r["market"],
                      "eid": r["eid"], "game": f"{away} @ {home}",
                      "kickoff": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(k)) if k else None,
                      "final": list(finals[(r["sport"], r["eid"])]) if (r["sport"], r["eid"]) in finals else None,
                      "need": r["need"], "strength": r.get("strength"), "need_bet": _bet(r.get("need_bet")), **src})
    records = []
    keyf = lambda g: (g["rule"], g["cut"], g["sport"], g["market"])  # noqa: E731
    by = collections.defaultdict(list)
    for g in games:
        by[keyf(g)].append(g)
    for (rule, cut, sport, market), gs in sorted(by.items()):
        for s in SOURCES:
            for cat in ORDER:
                sel = [g for g in gs if g[s] and g[s]["cat"] == cat]
                if not sel:
                    continue
                records.append({"rule": rule, "cut": cut, "sport": sport, "market": market, "source": s, "cat": cat,
                                "games": len(sel),
                                "need_side": _tally([g["need_bet"] for g in sel]) if cat in NEED_CATS else None,
                                "sharp_side": _tally([g[s]["sharp_bet"] for g in sel if g[s]["dir"]])})
            sel = [g for g in gs if g["need"]]                # the NEED alone, whatever the sharps did
            records.append({"rule": rule, "cut": cut, "sport": sport, "market": market, "source": s, "cat": "ALL_NEED",
                            "games": len(sel), "need_side": _tally([g["need_bet"] for g in sel]), "sharp_side": None})
    ko = [g["kickoff"] for g in games if g["kickoff"]]
    return {"version": "agreement_research_2026.v1", "generated": "2026-11-12",
            "source_commit": "9bb08c6 (research/original-intel: original_v3/agreement.py)",
            "games_from": min(ko) if ko else None, "games_to": max(ko) if ko else None,
            "status": "EXPLORATORY RESEARCH (retrospective replay; not frozen pre-game records)",
            "label": "SIMULATED sharps (synthetic sportsbook model output, not real money)",
            "rules": {"v3": "original freshness rule", "v4": "V4-compatible freshness rule"},
            "sources": {"genuine": "positions published before the cutoff (production ledger)",
                        "recon": "full-season replay of the same engine (reconstructed afterwards)"},
            "grading": "best real price at the cutoff, 1 unit risked; CLV vs the observed closing consensus",
            "records": records, "games": games}


def load(path=PATH):
    """The frozen research results, or None when the file is absent/unreadable (the tab then says so)."""
    try:
        with gzip.open(path, "rt") as fh:
            return json.load(fh)
    except (OSError, ValueError, EOFError):
        return None


def main(argv=None):
    import pickle
    ap = argparse.ArgumentParser()
    ap.add_argument("--agree", required=True)
    ap.add_argument("--replays", nargs="+", required=True, help="original_v3 replay outputs (meta: teams, kickoffs)")
    ap.add_argument("--out", default=PATH)
    a = ap.parse_args(argv)
    teams, finals = {}, {}
    for p in a.replays:
        rep = pickle.load(open(p, "rb"))
        for m in rep["meta"].values():
            teams[m["event_id"]] = (m["kickoff"], m["home"], m["away"])
        finals.update(rep["finals"])
    doc = export(pickle.load(open(a.agree, "rb")), teams, finals)
    with gzip.open(a.out, "wt") as fh:
        json.dump(doc, fh, separators=(",", ":"), sort_keys=True)
    print(len(doc["games"]), "game rows,", len(doc["records"]), "record rows ->", a.out)


if __name__ == "__main__":
    main()
