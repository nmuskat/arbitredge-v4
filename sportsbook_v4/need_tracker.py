"""
sportsbook_v4/need_tracker.py -- Book Need Tracker (RESEARCH / PAPER ONLY).

Question under test: when broad recreational pressure creates a large financial Need for the
synthetic sportsbook, does betting the side the book needs have a measurable advantage?

  * Need      -- from the book's ACTUAL simulated P&L: every accepted spread wager settled at its own
                 line and price (settlement.exposure). The Need side is the side whose cover leaves the
                 book better off.
  * Pressure  -- recreational (non-SHARP archetypes) tickets and handle by side, sharp handle,
                 largest-bettor concentration, counts, the game's spread-handle rank in its week.
  * Qualified -- the pre-registered research filter QUALIFY (experimental thresholds, NOT validated).
  * Alignment -- fading the recreational side and the book's Need agree (ALIGNED) or not (DISAGREE).
                 A disagreement is flagged, never forced into a pick.

Records (all immutable, all MODELED):
  * snapshot  -- one per priority game per dashboard build in the final SNAPSHOT_HORIZON_S before
                 kickoff, taken strictly before kickoff (by data time AND build wall-clock time).
  * decision  -- after kickoff, the LAST snapshot before kickoff is sealed as the game's decision
                 (or NO_PRE_KICKOFF_SNAPSHOT). Sealed once; a different second seal is an error.
Grading uses each decision's own recorded line and price against the real final score.
"""

import datetime
import glob
import gzip
import json
import math
import os
import time

from sportsbook_v4 import cohorts as C
from sportsbook_v4.grading import ats, binom_two_sided, wilson
from sportsbook_v4.market import implied_probability, profit_on_win
from sportsbook_v4.params import ARCHETYPES
from sportsbook_v4.settlement import exposure
from sportsbook_v4.util import json_hash, to_epoch

TRACKER_VERSION = "need_tracker_v1"
LABEL = "MODELED / RESEARCH ONLY"
SNAPSHOT_HORIZON_S = 24 * 3600
EARLY_DECISION_S = 3 * 3600          # a decision taken more than 3 h before kickoff is flagged EARLY
TRACKER_START = "2026-10-29T00:00:00Z"   # games kicking off before this were never snapshotted
MIN_SAMPLE_FOR_CLAIMS = 300

# PRE-REGISTERED 2026-10-29 (owner). Experimental thresholds, not validated profitability rules.
QUALIFY = {
    "weekly_handle_top_share": 0.25,     # top 25% of the week's games by spread handle (rank, point-in-time)
    "min_spread_wagers": 100,
    "max_single_bettor_share": 0.25,     # no bettor holds more than 25% of the game's spread handle
    "min_rec_handle_share": 0.70,        # >= 70% of recreational spread handle on one side
    "rec_ticket_majority": True,         # recreational ticket majority on that same side
}

SHARP = {a for a, v in ARCHETYPES.items() if v.get("is_sharp")}


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _norm(o):
    """The exact form records are stored and shown in (floats to 6 places, tuples as lists, str keys),
    so a record's hash survives any later rounding pass."""
    if isinstance(o, float):
        return None if o != o else round(o, 6)
    if isinstance(o, dict):
        return {str(k): _norm(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_norm(v) for v in o]
    return o


def _r(x, n=4):
    return None if x is None else round(x, n)


def pressure(spread_wagers):
    """Public-pressure metrics of one game's accepted SPREAD wagers."""
    ws = [w for w in spread_wagers if w["market"] == "spread"]
    handle = math.fsum(w["stake"] for w in ws)
    rec = [w for w in ws if w["archetype"] not in SHARP]
    sh = [w for w in ws if w["archetype"] in SHARP]
    rec_h = math.fsum(w["stake"] for w in rec)
    by_bettor = {}
    for w in ws:
        by_bettor[w["bettor_index"]] = by_bettor.get(w["bettor_index"], 0.0) + w["stake"]
    top = max(by_bettor.values(), default=0.0)

    def side(group, s, key):
        sel = [w for w in group if w["side"] == s]
        return len(sel) if key == "tickets" else math.fsum(w["stake"] for w in sel)

    out = {"wagers": len(ws), "bettors": len(by_bettor), "handle": round(handle, 2),
           "rec_wagers": len(rec), "rec_handle": round(rec_h, 2),
           "sharp_wagers": len(sh), "sharp_bettors": len({w["bettor_index"] for w in sh}),
           "sharp_handle": round(handle - rec_h, 2),
           "sharp_handle_share": _r((handle - rec_h) / handle) if handle else None,
           "largest_bettor_handle": round(top, 2),
           "largest_bettor_share": _r(top / handle) if handle else None,
           "sides": {}}
    for s in ("home", "away"):
        rt, rh = side(rec, s, "tickets"), side(rec, s, "handle")
        out["sides"][s] = {
            "tickets": side(ws, s, "tickets"), "handle": round(side(ws, s, "handle"), 2),
            "rec_tickets": rt, "rec_handle": round(rh, 2),
            "rec_ticket_share": _r(rt / len(rec)) if rec else None,
            "rec_handle_share": _r(rh / rec_h) if rec_h else None,
            "sharp_handle": round(side(sh, s, "handle"), 2)}
    return out


def week_rank(handles, gid):
    """(rank 1 = largest, n) of a game's spread handle among its week's games at the same moment."""
    v = handles.get(gid, 0.0)
    return 1 + sum(1 for g, h in handles.items() if g != gid and h > v), len(handles)


def qualify(p, rank, n):
    """The pre-registered research filter. Returns {qualified, public_side, fade_side, checks}."""
    top_n = max(1, math.ceil(QUALIFY["weekly_handle_top_share"] * n))
    sides = p["sides"]
    public = max(("home", "away"), key=lambda s: (sides[s]["rec_handle_share"] or 0.0, s == "home"))
    hs, ts = sides[public]["rec_handle_share"], sides[public]["rec_ticket_share"]
    checks = {
        "weekly_handle_top_25pct": {"pass": rank <= top_n, "value": f"rank {rank} of {n} (top {top_n})"},
        "min_100_spread_wagers": {"pass": p["wagers"] >= QUALIFY["min_spread_wagers"], "value": p["wagers"]},
        "no_bettor_over_25pct": {"pass": p["largest_bettor_share"] is not None
                                 and p["largest_bettor_share"] <= QUALIFY["max_single_bettor_share"],
                                 "value": p["largest_bettor_share"]},
        "rec_handle_70pct_one_side": {"pass": hs is not None and hs >= QUALIFY["min_rec_handle_share"], "value": hs},
        "rec_ticket_majority_same_side": {"pass": ts is not None and ts > 0.5, "value": ts},
    }
    return {"qualified": all(c["pass"] for c in checks.values()), "public_side": public if hs else None,
            "fade_side": ("away" if public == "home" else "home") if hs else None, "checks": checks}


def need_of(spread_wagers):
    e = exposure(spread_wagers)
    if e.get("status") != "DIRECTIONAL":
        return {"status": e.get("status"), "side": None}
    return {"status": "DIRECTIONAL", "side": e["financial_need_side"],
            "pnl_if_home_covers_all": e["pnl_if_home_covers_all"], "pnl_if_away_covers_all": e["pnl_if_away_covers_all"],
            "swing": e["pnl_swing"], "worst_case_pnl": e["worst_case_pnl"],
            "worst_case_margins": e["worst_case_margins"],
            # the book's loss if the side it does NOT need covers every accepted number
            "liability": round(min(e["pnl_if_home_covers_all"], e["pnl_if_away_covers_all"]), 2)}


def alignment(need_side, fade_side):
    if need_side is None:
        return "NO_NEED"
    if fade_side is None:
        return "NO_PUBLIC_SIDE"
    return "ALIGNED" if need_side == fade_side else "DISAGREE"


def snapshot(*, sport, run, game, spread_wagers, week_handles, offer, reference, taken_at, built_at, extra=None):
    """One immutable pre-kickoff record. taken_at: the ledger's data time; built_at: wall clock."""
    kick = to_epoch(game["kickoff"])
    if not (taken_at < kick and built_at < kick):
        raise ValueError("snapshot must be taken before kickoff")
    p = pressure(spread_wagers)
    rank, n = week_rank(week_handles, game["event_id"])
    q = qualify(p, rank, n)
    nd = need_of(spread_wagers)
    al = alignment(nd["side"], q["fade_side"])
    line = price = None
    if offer and nd["side"]:
        line = offer["home_spread"] if nd["side"] == "home" else -offer["home_spread"]
        price = offer["home_price"] if nd["side"] == "home" else offer["away_price"]
    rec = {
        "format": TRACKER_VERSION, "label": LABEL, "sport": sport, "event_id": game["event_id"],
        "home": game["home"], "away": game["away"], "kickoff": _iso(kick), "rankings": game.get("rankings"),
        "taken_at": _iso(taken_at), "built_at": _iso(built_at), "minutes_before_kickoff": round((kick - taken_at) / 60),
        "run_id": run["run_id"], "engine_version": run["engine_version"], "params_hash": run["params_hash"],
        "wager_ids_hash": json_hash(sorted(w["wager_id"] for w in spread_wagers)),
        "pressure": p, "week_handle_rank": rank, "week_games": n,
        "week_handle_percentile": _r(1 - (rank - 1) / n) if n else None,
        "qualification": q, "need": nd, "alignment": al,
        # the side the tracker grades: the book's Need, at the market price shown to customers then
        "need_pick": {"side": nd["side"], "line": line, "price": price,
                      "price_book": (offer or {}).get("price_book"), "offer": offer},
        # the research subset: qualified AND fading the public is the side the book needs
        "research_pick": nd["side"] if (q["qualified"] and al == "ALIGNED") else None,
        "reference": reference, "thresholds": QUALIFY,
    }
    rec.update(extra or {})     # informational (V5: need_stability, ledger_basis); never used by cohort rules
    # pre-registered cohorts: evaluated NOW, stored in the immutable record (never re-evaluated later)
    rec["cohorts"] = None
    if taken_at >= to_epoch(C.REGISTERED_AT):
        rec["cohorts"] = {"registry_hash": C.REGISTRY_HASH, "registered_at": C.REGISTERED_AT,
                          "evaluation": C.evaluate(rec)}
    rec = _norm(rec)
    rec["record_hash"] = json_hash(rec)
    return rec


# ------------------------------------------------------------------ immutable files

class TrackerIntegrityError(RuntimeError):
    pass


def _verify(rec, where):
    body = {k: v for k, v in rec.items() if k != "record_hash"}
    if json_hash(body) != rec.get("record_hash"):
        raise TrackerIntegrityError(f"{where}: record hash mismatch for {rec.get('event_id')}")


def write(directory, name, records):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    if os.path.exists(path):
        raise TrackerIntegrityError(f"{name} already exists: files are never overwritten")
    doc = {"format": TRACKER_VERSION, "records": records, "records_hash": json_hash(records)}
    with gzip.open(path, "wt") as f:
        json.dump(doc, f, sort_keys=True)
    return path


def load(directory, pattern):
    """Every record in matching files, each file and record hash-verified."""
    out = []
    for path in sorted(glob.glob(os.path.join(directory, "**", pattern), recursive=True)) if directory else []:
        with gzip.open(path, "rt") as f:
            doc = json.load(f)
        if json_hash(doc["records"]) != doc.get("records_hash"):
            raise TrackerIntegrityError(f"{os.path.basename(path)}: file hash mismatch")
        for r in doc["records"]:
            _verify(r, os.path.basename(path))
            out.append(r)
    return out


def snapshot_name(sport, built_at):
    return f"need_{sport}_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(built_at))}.json.gz"


def decisions_name(built_at):
    return f"decisions_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(built_at))}.json.gz"


def release_for(t):
    """Weekly release of raw snapshots (ISO week), e.g. v4-need-2026-W44."""
    y, w, _ = datetime.date(*time.gmtime(t)[:3]).isocalendar()
    return f"v4-need-{y}-W{w:02d}"


def seal(games, snapshots, decisions, now):
    """New decision records for games that have kicked off and are not sealed yet.
    games: [{sport, event_id, home, away, kickoff}] -- every priority game the book carried."""
    done = {}
    for d in decisions:
        k = (d["sport"], d["event_id"])
        if k in done and done[k]["record_hash"] != d["record_hash"]:
            raise TrackerIntegrityError(f"two different decisions sealed for {k}")
        done[k] = d
    by_game = {}
    for s in snapshots:
        by_game.setdefault((s["sport"], s["event_id"]), []).append(s)
    new = []
    for g in sorted(games, key=lambda x: (x["kickoff"], x["sport"], x["event_id"])):
        k = (g["sport"], g["event_id"])
        kick = to_epoch(g["kickoff"])
        if k in done or kick > now or kick < to_epoch(TRACKER_START):
            continue
        pre = [s for s in by_game.get(k, []) if to_epoch(s["taken_at"]) < kick and to_epoch(s["built_at"]) < kick]
        last = max(pre, key=lambda s: (s["taken_at"], s["built_at"]), default=None)
        rec = {"format": TRACKER_VERSION, "sport": g["sport"], "event_id": g["event_id"], "home": g["home"],
               "away": g["away"], "kickoff": _iso(kick), "sealed_at": _iso(now),
               "status": ("NO_PRE_KICKOFF_SNAPSHOT" if last is None else
                          "EARLY" if kick - to_epoch(last["taken_at"]) > EARLY_DECISION_S else "OK"),
               "snapshot": last}
        rec = _norm(rec)
        rec["record_hash"] = json_hash(rec)
        new.append(rec)
        done[k] = rec
    return new


# ------------------------------------------------------------------ grading

def grade(decision, result):
    """One decision against a final score (result: {home_score, away_score} or None)."""
    s = decision.get("snapshot") or {}
    pick = s.get("need_pick") or {}
    out = {"sport": decision["sport"], "event_id": decision["event_id"], "home": decision["home"],
           "away": decision["away"], "kickoff": decision["kickoff"], "decision_status": decision["status"],
           "need_side": pick.get("side"), "line": pick.get("line"), "price": pick.get("price"),
           "price_book": pick.get("price_book"), "qualified": (s.get("qualification") or {}).get("qualified"),
           "alignment": s.get("alignment"), "research_pick": s.get("research_pick"),
           "largest_bettor_share": (s.get("pressure") or {}).get("largest_bettor_share"),
           "week_handle_percentile": s.get("week_handle_percentile"),
           "rec_public_share": None, "minutes_before_kickoff": s.get("minutes_before_kickoff")}
    q = s.get("qualification") or {}
    if q.get("public_side"):
        out["rec_public_share"] = s["pressure"]["sides"][q["public_side"]]["rec_handle_share"]
    if decision["status"] == "NO_PRE_KICKOFF_SNAPSHOT" or not pick.get("side"):
        out["status"] = "NO_DECISION" if decision["status"] == "NO_PRE_KICKOFF_SNAPSHOT" else "NO_NEED"
        return out
    if pick.get("line") is None:
        out["status"] = "NO_PRICE"
        return out
    if result is None:
        out["status"] = "AWAITING_RESULT"
        return out
    out["status"] = "GRADED"
    out["home_score"], out["away_score"] = result["home_score"], result["away_score"]
    out["ats"] = ats(pick["side"], pick["line"], result["home_score"], result["away_score"])
    if pick.get("price") is not None:
        out["units"] = (profit_on_win(1.0, pick["price"]) if out["ats"] == "W" else -1.0 if out["ats"] == "L" else 0.0)
    # the side fading the public would have taken, when it differs from the Need (informational)
    if q.get("fade_side") and q["fade_side"] != pick["side"]:
        out["fade_ats"] = {"W": "L", "L": "W", "P": "P"}[out["ats"]]
    return out


def summarize(rows):
    g = [r for r in rows if r["status"] == "GRADED"]
    w, l, p = (sum(r["ats"] == x for r in g) for x in "WLP")
    priced = [r for r in g if "units" in r]
    units = [r["units"] for r in priced]
    out = {"games": len(rows), "graded": len(g), "record": f"{w}-{l}-{p}", "wins": w, "losses": l, "pushes": p,
           "win_pct_ex_push": _r(w / (w + l)) if w + l else None, "win_pct_95ci": wilson(w, w + l),
           "p_value_vs_50pct": binom_two_sided(w, w + l) if w + l else None,
           "awaiting_result": sum(r["status"] == "AWAITING_RESULT" for r in rows),
           "not_gradable": {s: sum(r["status"] == s for r in rows) for s in ("NO_DECISION", "NO_NEED", "NO_PRICE")}}
    if units:
        mean = math.fsum(units) / len(units)
        sd = math.sqrt(math.fsum((u - mean) ** 2 for u in units) / (len(units) - 1)) if len(units) > 1 else None
        half = 1.96 * sd / math.sqrt(len(units)) if sd is not None else None
        out["roi"] = {"n": len(units), "units": round(math.fsum(units), 3), "roi": _r(mean),
                      "roi_95ci": [_r(mean - half), _r(mean + half)] if half is not None else None,
                      "breakeven_win_pct": _r(math.fsum(implied_probability(r["price"]) for r in priced) / len(priced))}
    out["sample_warning"] = (None if w + l >= MIN_SAMPLE_FOR_CLAIMS else
                             f"INSUFFICIENT SAMPLE ({w + l} decided < {MIN_SAMPLE_FOR_CLAIMS}): no performance claim")
    return out


def _side_offer(offer, side):
    if not offer or side not in ("home", "away") or offer.get("home_spread") is None:
        return None, None
    return (offer["home_spread"] if side == "home" else -offer["home_spread"],
            offer["home_price"] if side == "home" else offer["away_price"])


def clv(side, line, price, close):
    """Closing-line value against the OBSERVED closing consensus (asof.observed_close of spreads):
    points = recorded line - closing line for the same side; prob = devigged closing probability minus the
    recorded price's implied probability, only when the recorded line equals the closing line."""
    if not close or close.get("line") is None or line is None:
        return {"close": "NOT_OBSERVED"}
    cl = close["line"] if side == "home" else -close["line"]
    out = {"close": "OBSERVED", "closing_line": cl, "clv_points": round(line - cl, 2)}
    if price is not None and line == cl and close.get("fair"):
        out["clv_prob"] = round(close["fair"][side] - implied_probability(price), 4)
    return out


def grade_cohort(decision, cid, result, close):
    s = decision.get("snapshot") or {}
    ev = ((s.get("cohorts") or {}).get("evaluation") or {}).get(cid)
    if not ev or not ev["eligible"]:
        return None
    side = ev["pick_side"]
    line, price = _side_offer((s.get("need_pick") or {}).get("offer"), side)
    out = {"sport": decision["sport"], "event_id": decision["event_id"], "home": decision["home"],
           "away": decision["away"], "kickoff": decision["kickoff"], "cohort": cid, "pick_side": side,
           "line": line, "price": price, "pick_is_need_side": ev.get("pick_is_need_side"),
           "decision_status": decision["status"], **clv(side, line, price, close)}
    if line is None:
        out["status"] = "NO_PRICE"
    elif result is None:
        out["status"] = "AWAITING_RESULT"
    else:
        out["status"] = "GRADED"
        out["ats"] = ats(side, line, result["home_score"], result["away_score"])
        if price is not None:
            out["units"] = profit_on_win(1.0, price) if out["ats"] == "W" else -1.0 if out["ats"] == "L" else 0.0
    return out


def _p_edge(rows):
    """One-sided exact binomial p-value of the win count against the break-even rate of the recorded prices."""
    g = [r for r in rows if r["status"] == "GRADED" and r["ats"] != "P" and r.get("price") is not None]
    if not g:
        return None
    w, n = sum(r["ats"] == "W" for r in g), len(g)
    p0 = math.fsum(implied_probability(r["price"]) for r in g) / n
    return min(1.0, math.fsum(math.comb(n, k) * p0 ** k * (1 - p0) ** (n - k) for k in range(w, n + 1)))


def holm(pvals):
    """Holm-Bonferroni adjusted p-values for {name: p or None}."""
    items = sorted(((p, k) for k, p in pvals.items() if p is not None))
    m, out, run = len(items), {k: None for k in pvals}, 0.0
    for i, (p, k) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = round(run, 4)
    return out


def clv_summary(rows):
    obs = [r for r in rows if r.get("close") == "OBSERVED"]
    pr = [r["clv_prob"] for r in obs if "clv_prob" in r]
    pts = [r["clv_points"] for r in obs]
    out = {"picks": len(rows), "close_observed": len(obs), "close_observed_share": _r(len(obs) / len(rows)) if rows else None,
           "mean_clv_points": _r(math.fsum(pts) / len(pts)) if pts else None,
           "share_beating_close_points": _r(sum(x > 0 for x in pts) / len(pts)) if pts else None,
           "same_line_n": len(pr), "mean_clv_prob": _r(math.fsum(pr) / len(pr)) if pr else None}
    if len(pr) > 1:
        m = math.fsum(pr) / len(pr)
        sd = math.sqrt(math.fsum((x - m) ** 2 for x in pr) / (len(pr) - 1))
        out["clv_prob_95ci"] = [_r(m - 1.96 * sd / math.sqrt(len(pr))), _r(m + 1.96 * sd / math.sqrt(len(pr)))]
    return out


def cohort_report(decisions, results, closes):
    rows = {cid: [] for cid in C.COHORTS}
    for d in decisions:
        for cid in C.COHORTS:
            r = grade_cohort(d, cid, results.get((d["sport"], d["event_id"])), closes.get((d["sport"], d["event_id"])))
            if r:
                rows[cid].append(r)
    p_raw = {cid: _p_edge(v) for cid, v in rows.items()}
    p_adj = holm(p_raw)
    eng = {}
    for d in decisions:
        eng[(d["sport"], d["event_id"])] = ((d.get("snapshot") or {}).get("engine_version") or "unknown")
    return {"registry_hash": C.REGISTRY_HASH, "registered_at": C.REGISTERED_AT,
            "multiple_testing": "Holm-Bonferroni across all registered cohorts; only C1 is primary",
            "by_engine_note": "rules unchanged; results also shown separately for each sportsbook engine",
            "cohorts": {cid: {**{k: v for k, v in C.COHORTS[cid].items() if k != "rules"}, "rules": C.COHORTS[cid]["rules"],
                              "summary": summarize(v), "clv": clv_summary(v),
                              "p_edge_one_sided": _r(p_raw[cid]), "p_edge_holm": p_adj[cid],
                              "by_engine": {e: summarize([r for r in v if eng.get((r["sport"], r["event_id"])) == e])
                                            for e in sorted({eng.get((r["sport"], r["event_id"])) for r in v})},
                              "rows": v}
                        for cid, v in rows.items()}}


def _bucket_share(x):
    if x is None:
        return "unknown"
    return "<10%" if x < 0.10 else "10-25%" if x <= 0.25 else ">25%"


def _bucket_pct(x):
    if x is None:
        return "unknown"
    return "top 25%" if x > 0.75 else "50-75%" if x > 0.5 else "bottom 50%"


def report(decisions, results, closes=None):
    """results: {(sport, event_id): {home_score, away_score, ...}}; closes: observed closing spread consensus."""
    rows = [grade(d, results.get((d["sport"], d["event_id"]))) for d in decisions]
    research = [r for r in rows if r["research_pick"]]
    disagree = [r for r in rows if r["qualified"] and r["alignment"] == "DISAGREE"]
    out = {"version": TRACKER_VERSION, "label": LABEL, "thresholds": QUALIFY,
           "cohorts": cohort_report(decisions, results, closes or {}),
           "all_need": summarize(rows), "research_subset": summarize(research),
           "qualified_disagreements": {"games": len(disagree),
                                       "note": "qualified public pressure, but the book's actual Need is on the "
                                               "public side (e.g. sharp money or line positions): flagged, not picked",
                                       "need_side": summarize(disagree)},
           "by_largest_bettor_share": {}, "by_week_handle_percentile": {}, "by_sport": {}, "rows": rows}
    for key, fn, field in (("by_largest_bettor_share", _bucket_share, "largest_bettor_share"),
                           ("by_week_handle_percentile", _bucket_pct, "week_handle_percentile"),
                           ("by_sport", lambda x: x, "sport")):
        b = {}
        for r in rows:
            b.setdefault(fn(r[field]), []).append(r)
        out[key] = {k: summarize(v) for k, v in sorted(b.items())}
    return out
