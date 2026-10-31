"""
official_v5/rules.py -- the Official ArbitrEdge Needs rules (version below), evaluation, freezing, sealing, grading.

EXPERIMENTAL research thresholds, not proven betting signals. Fixed in advance; changing any value requires a new
RULES_VERSION (the hash of RULES is stored in every record and pinned by a test). Never lowered to produce plays.

A game is OFFICIAL only when all six rules pass (an EXPERIMENTAL MODEL SIGNAL; the site's decision stays NO BET):
  R1 stable_need      the spread Book Need side is the same under the baseline and all four sensitivity assumptions
  R2 top_handle       the game's spread handle ranks in the top 25% of the week's games (rank <= ceil(n / 4))
  R3 rec_opposes      at least 65% of recreational spread handle AND a majority (> 50%) of recreational spread
                      tickets are on the side opposite the Need
  R4 sharp_supports   at least 60% of sharp spread handle is on the Need side, with sample safeguards: >= 3 sharp
                      wagers, >= 2 sharp customers, no sharp customer above 70% of sharp handle, sharp handle >= 5% of
                      the game's spread handle
  R5 top_swing        the book's spread P&L swing ranks in the top 25% of the week's games with a directional Need
  R6 data_valid       directional Need; a priced market offer; odds observed within 3 h of the ledger time; ledger
                      time within 3 h of the build; evaluated within 24 h before kickoff; >= 100 distinct spread
                      bettors and no single bettor above 15% of the game's spread handle
WATCHLIST: R1 and R6 pass and at least three of R2-R5 pass. PASS: everything else (reasons listed).
Sealing: after kickoff (+20 min) the LAST record taken and built before kickoff decides; if it is older than 180 minutes
before kickoff the game is sealed NO_FINAL_EVALUATION. Every sealed record states its data quality.
"""

import math
import time

from sportsbook_v4 import need_tracker as NT
from sportsbook_v4.market import profit_on_win
from sportsbook_v4.grading import ats
from sportsbook_v4.util import json_hash, to_epoch

RULES_VERSION = "official_needs.v1"
REGISTERED_AT = "2026-10-31T04:00:00Z"   # records are created only by builds at/after this time, before kickoff
LABEL = ("EXPERIMENTAL MODEL SIGNALS: versioned research rules applied to the MODELED synthetic sportsbook. Not a "
         "proven betting signal, not an official play under rebuild plan section G, and not an instruction to wager. "
         "Default decision: NO BET.")
RULES = {
    "stable_need": {"variants_required": 4},
    "top_handle": {"top_share": 0.25},
    "rec_opposes": {"min_rec_handle_share": 0.65, "min_rec_ticket_share_exclusive": 0.50},
    "sharp_supports": {"min_sharp_handle_share": 0.60, "min_sharp_wagers": 3, "min_sharp_bettors": 2,
                       "max_one_sharp_share": 0.70, "min_sharp_share_of_handle": 0.05},
    "top_swing": {"top_share": 0.25},
    "data_valid": {"max_odds_age_h": 3, "max_ledger_lag_h": 3, "max_hours_before_kickoff": 24,
                   "min_distinct_bettors": 100, "max_largest_bettor_share": 0.15},
    "watchlist": {"requires": ["stable_need", "data_valid"], "min_other_passes": 3},
    # the sealed classification comes only from a FINAL evaluation: the last record before kickoff must be taken within
    # this many minutes of kickoff, otherwise the game is sealed NO_FINAL_EVALUATION (an older OFFICIAL is never reused)
    "seal": {"final_record_max_minutes_before_kickoff": 180},
}
RULES_HASH = json_hash(RULES)
NAMES = {"stable_need": "Stable Book Need (all 4 sensitivity assumptions)", "top_handle": "Top 25% weekly spread handle",
         "rec_opposes": "Recreational money opposes the Need (>= 65% handle, ticket majority)",
         "sharp_supports": "Sharp money supports the Need (>= 60% of sharp handle, sample safeguards)",
         "top_swing": "Top 25% sportsbook spread P&L swing", "data_valid": "Fresh, valid pre-kickoff data, adequate bettors"}
ORDER = ("stable_need", "top_handle", "rec_opposes", "sharp_supports", "top_swing", "data_valid")
SNAP_PREFIX, DEC_PREFIX = "official_snap_", "official_dec_"
SEAL_GRACE_S = NT.SEAL_GRACE_S


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _rank(values, key):
    """1-based rank of key by value, descending (ties share the better rank); n = number of entries."""
    v = values.get(key)
    if v is None:
        return None, len(values)
    return 1 + sum(1 for x in values.values() if x > v), len(values)


def week_inputs(spread_by_game):
    """Per-week inputs from every game's accepted spread wagers: handle (all games) and P&L swing (directional)."""
    handles = {g: math.fsum(w["stake"] for w in ws) for g, ws in spread_by_game.items() if ws}
    swings = {}
    for g, ws in spread_by_game.items():
        nd = NT.need_of(ws) if ws else {"status": None}
        if nd.get("status") == "DIRECTIONAL" and nd.get("swing") is not None:
            swings[g] = nd["swing"]
    return {"handles": handles, "swings": swings}


def _sharp(spread, need_side):
    sh = [w for w in spread if w["archetype"] in NT.SHARP]
    total = math.fsum(w["stake"] for w in spread)
    sh_h = math.fsum(w["stake"] for w in sh)
    per = {}
    for w in sh:
        per[w["bettor_index"]] = per.get(w["bettor_index"], 0.0) + w["stake"]
    on = math.fsum(w["stake"] for w in sh if w["side"] == need_side)
    return {"sharp_wagers": len(sh), "sharp_bettors": len(per), "sharp_handle": round(sh_h, 2),
            "sharp_share_of_handle": round(sh_h / total, 4) if total else None,
            "largest_sharp_share": round(max(per.values()) / sh_h, 4) if sh_h else None,
            "need_side_share": round(on / sh_h, 4) if sh_h else None}


def evaluate(*, game, spread_wagers, week, need_stability, offer, odds_as_of, taken_at, built_at, enforce_window=True):
    """Rule results for one game (pure; no side effects). Returns {classification, pick, rules, supporting}.
    enforce_window=False is used only for the provisional display of games more than 24 h away (never frozen)."""
    R = RULES
    kick = to_epoch(game["kickoff"])
    spread = [w for w in spread_wagers if w["market"] == "spread"]
    nd = NT.need_of(spread) if spread else {"status": "NO_WAGERS", "side": None}
    side = nd.get("side")
    other = {"home": "away", "away": "home"}.get(side)
    p = NT.pressure(spread) if spread else None
    rules = {}

    ns = need_stability or {}
    rules["stable_need"] = {"pass": bool(side) and ns.get("label") == "STABLE" and ns.get("baseline") == side
                            and ns.get("variants") == R["stable_need"]["variants_required"],
                            "value": f"{ns.get('agree')}/{ns.get('variants')} assumptions agree" if ns.get("variants") else "not available"}

    hr, hn = _rank(week["handles"], game["event_id"])
    cut = max(1, math.ceil(hn * R["top_handle"]["top_share"])) if hn else 0
    rules["top_handle"] = {"pass": hr is not None and hr <= cut, "value": f"rank {hr} of {hn} (top {cut})" if hr else "no handle"}

    if p and other:
        rh = p["sides"][other]["rec_handle_share"]
        rt = p["sides"][other]["rec_ticket_share"]
        t = R["rec_opposes"]
        rules["rec_opposes"] = {"pass": rh is not None and rt is not None and rh >= t["min_rec_handle_share"]
                                and rt > t["min_rec_ticket_share_exclusive"],
                                "value": f"{_pct(rh)} of rec handle, {_pct(rt)} of rec tickets against the Need"}
    else:
        rules["rec_opposes"] = {"pass": False, "value": "no directional Need"}

    sx = _sharp(spread, side) if side else None
    t = R["sharp_supports"]
    if sx:
        why = []
        if sx["sharp_wagers"] < t["min_sharp_wagers"]:
            why.append(f"{sx['sharp_wagers']} sharp wagers (min {t['min_sharp_wagers']})")
        if sx["sharp_bettors"] < t["min_sharp_bettors"]:
            why.append(f"{sx['sharp_bettors']} sharp customers (min {t['min_sharp_bettors']})")
        if sx["largest_sharp_share"] is not None and sx["largest_sharp_share"] > t["max_one_sharp_share"]:
            why.append(f"one sharp customer {_pct(sx['largest_sharp_share'])} of sharp handle (max {_pct(t['max_one_sharp_share'])})")
        if (sx["sharp_share_of_handle"] or 0) < t["min_sharp_share_of_handle"]:
            why.append(f"sharp handle {_pct(sx['sharp_share_of_handle'])} of the game (min {_pct(t['min_sharp_share_of_handle'])})")
        ok = not why and (sx["need_side_share"] or 0) >= t["min_sharp_handle_share"]
        rules["sharp_supports"] = {"pass": ok, "value": f"{_pct(sx['need_side_share'])} of sharp handle on the Need side"
                                   + (" (" + "; ".join(why) + ")" if why else "")}
    else:
        rules["sharp_supports"] = {"pass": False, "value": "no directional Need"}

    sr, sn = _rank(week["swings"], game["event_id"])
    cut = max(1, math.ceil(sn * R["top_swing"]["top_share"])) if sn else 0
    directional = nd.get("status") == "DIRECTIONAL" and nd.get("swing") is not None
    rules["top_swing"] = {"pass": directional and sr is not None and sr <= cut,
                          "value": f"swing ${nd['swing']:,.0f}, rank {sr} of {sn} (top {cut})" if directional and sr
                          else "no directional Need"}

    t = R["data_valid"]
    line = price = None
    if offer and side:
        line = offer["home_spread"] if side == "home" else -offer["home_spread"]
        price = offer["home_price"] if side == "home" else offer["away_price"]
    why = []
    if nd.get("status") != "DIRECTIONAL":
        why.append(f"Need {nd.get('status')}")
    if line is None or price is None:
        why.append("no priced market offer")
    if odds_as_of is None or taken_at - odds_as_of > t["max_odds_age_h"] * 3600:
        why.append("odds older than 3 h")
    if built_at - taken_at > t["max_ledger_lag_h"] * 3600:
        why.append("ledger more than 3 h behind the build")
    if not (taken_at < kick and built_at < kick):
        why.append("not before kickoff")
    elif enforce_window and kick - taken_at > t["max_hours_before_kickoff"] * 3600:
        why.append("more than 24 h before kickoff")
    if not p or p["bettors"] < t["min_distinct_bettors"]:
        why.append(f"{p['bettors'] if p else 0} distinct spread bettors (min {t['min_distinct_bettors']})")
    elif (p["largest_bettor_share"] or 0) > t["max_largest_bettor_share"]:
        why.append(f"largest bettor {_pct(p['largest_bettor_share'])} of handle (max {_pct(t['max_largest_bettor_share'])})")
    rules["data_valid"] = {"pass": not why, "value": "; ".join(why) or "fresh and valid"}

    passed = [k for k in ORDER if rules[k]["pass"]]
    others = sum(rules[k]["pass"] for k in ("top_handle", "rec_opposes", "sharp_supports", "top_swing"))
    if len(passed) == len(ORDER):
        cls = "OFFICIAL"
    elif all(rules[k]["pass"] for k in R["watchlist"]["requires"]) and others >= R["watchlist"]["min_other_passes"]:
        cls = "WATCHLIST"
    else:
        cls = "PASS"
    return {"classification": cls,
            "pick": {"side": side, "team": game.get(side) if side else None, "line": line, "price": price,
                     "price_book": (offer or {}).get("price_book"), "offer": offer},
            "rules": [{"id": k, "name": NAMES[k], **rules[k]} for k in ORDER],
            "failed": [NAMES[k] for k in ORDER if not rules[k]["pass"]],
            "supporting": {"need": {k: nd.get(k) for k in ("status", "side", "swing", "liability", "worst_case_pnl")},
                           "pressure": p, "sharp": sx, "need_stability": ns,
                           "week_handle_rank": hr, "week_games": hn, "swing_rank": sr, "swing_games": sn,
                           "odds_as_of": _iso(odds_as_of) if odds_as_of else None}}


def _pct(x):
    return "–" if x is None else f"{100 * x:.1f}%"


def snapshot(*, sport, run, game, evaluation, taken_at, built_at):
    """One immutable pre-kickoff record (only at/after REGISTERED_AT and before kickoff)."""
    kick = to_epoch(game["kickoff"])
    if not (taken_at < kick and built_at < kick):
        raise ValueError("an Official Needs record must be taken before kickoff")
    if taken_at < to_epoch(REGISTERED_AT):
        raise ValueError("before the rules were registered: never retroactive")
    rec = {"format": RULES_VERSION, "rules_hash": RULES_HASH, "label": LABEL, "sport": sport,
           "event_id": game["event_id"], "home": game["home"], "away": game["away"], "kickoff": _iso(kick),
           "taken_at": _iso(taken_at), "built_at": _iso(built_at), "minutes_before_kickoff": round((kick - taken_at) / 60),
           "run_id": run["run_id"], "engine_version": run["engine_version"], "params_hash": run["params_hash"],
           **evaluation}
    rec = NT._norm(rec)
    rec["record_hash"] = json_hash(rec)
    return rec


def snapshot_name(sport, t):
    return f"{SNAP_PREFIX}{sport}_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(t))}.json.gz"


def decisions_name(t):
    return f"{DEC_PREFIX}{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(t))}.json.gz"


def strategy_of(x):
    """The strategy a record or decision belongs to (v1 records carry only their format)."""
    return x.get("strategy") or x.get("format") or RULES_VERSION


def _key(x):
    return (x["sport"], x["event_id"], strategy_of(x))


def _seal_window(strategy):
    if strategy == RULES_VERSION:
        return RULES["seal"]["final_record_max_minutes_before_kickoff"]
    from official_v5 import strategies as S
    return S.ALL[strategy]["seal"]["final_record_max_minutes_before_kickoff"]


def seal(snapshots, decisions, now):
    """Freeze, for each game and strategy that has kicked off (plus a 20-minute grace) and has at least one prospective
    record, the LAST record taken and built before kickoff. Games without a record get no selection (never
    retroactive). Each strategy (official_needs.v1, spread.v2, total.v1) is sealed on its own records only."""
    done = {_key(d): d for d in decisions}
    by = {}
    for s in snapshots:
        by.setdefault(_key(s), []).append(s)
    new = []
    for k, ss in sorted(by.items()):
        if k in done:
            continue
        kick = to_epoch(max(ss, key=lambda s: s["built_at"])["kickoff"])     # the latest known kickoff (postponements)
        if kick + SEAL_GRACE_S > now:
            continue
        pre = [s for s in ss if to_epoch(s["taken_at"]) < kick and to_epoch(s["built_at"]) < kick]
        if not pre:
            continue
        last = max(pre, key=lambda s: (s["built_at"], s["taken_at"]))
        cls, dq = last["classification"], data_quality(last)
        win = _seal_window(k[2])
        if last["minutes_before_kickoff"] > win:
            cls = "NO_FINAL_EVALUATION"
            dq = (f"no evaluation in the final {win} minutes before kickoff (last one {last['minutes_before_kickoff']} "
                  f"min before); its {last['classification']} is not used")
        d = {"format": k[2], "sport": k[0], "event_id": k[1], "home": last["home"], "away": last["away"],
             "kickoff": last["kickoff"], "sealed_at": _iso(now), "classification": cls, "data_quality": dq,
             "snapshot": last}
        if k[2] != RULES_VERSION:                       # later strategies name themselves; v1 decisions are unchanged
            d.update(strategy=k[2], market=last.get("market"),
                     tier=last.get("tier") if cls == "OFFICIAL" else None, score=last.get("score"))
        d["record_hash"] = json_hash(d)
        new.append(d)
    return new


def data_quality(rec):
    """'VALID', or why the evaluation's data failed rule R6 (stale odds, ledger lag, too few bettors, ...)."""
    r = next((x for x in rec.get("rules") or [] if x["id"] == "data_valid"), None)
    return "VALID" if r and r["pass"] else f"INVALID: {(r or {}).get('value') or 'not evaluated'}"


def unique(decisions):
    """One sealed decision per game; two different seals of the same game are an integrity failure."""
    out = {}
    for d in sorted(decisions, key=lambda x: x["sealed_at"]):
        k = _key(d)
        if k in out and out[k]["snapshot"]["record_hash"] != d["snapshot"]["record_hash"]:
            raise NT.TrackerIntegrityError(f"two different Official Needs decisions for {k}")
        out.setdefault(k, d)
    return list(out.values())


def grade(d, result):
    s = d["snapshot"]
    pk = s["pick"]
    out = {"sport": d["sport"], "event_id": d["event_id"], "home": d["home"], "away": d["away"], "kickoff": d["kickoff"],
           "classification": d["classification"], "team": pk.get("team"), "side": pk.get("side"), "line": pk.get("line"),
           "price": pk.get("price"), "price_book": pk.get("price_book"), "taken_at": s["taken_at"],
           "minutes_before_kickoff": s["minutes_before_kickoff"], "engine_version": s["engine_version"],
           "rules_version": s["format"]}
    out.update(strategy=strategy_of(d), market=d.get("market") or "spread", tier=d.get("tier"), score=d.get("score"),
               failed=s.get("failed") or [])
    if not pk.get("side") or pk.get("line") is None or pk.get("price") is None:
        out["status"] = "NO_PRICE"
    elif result is None:
        out["status"] = "AWAITING_RESULT"
    elif out["market"] == "total":                  # over/under against the frozen total (a push when equal)
        t = result["home_score"] + result["away_score"]
        x = (t - pk["line"]) if pk["side"] == "over" else (pk["line"] - t)
        out.update(status="GRADED", home_score=result["home_score"], away_score=result["away_score"],
                   ats="W" if x > 0 else ("P" if x == 0 else "L"))
    else:
        out.update(status="GRADED", home_score=result["home_score"], away_score=result["away_score"],
                   ats=ats(pk["side"], pk["line"], result["home_score"], result["away_score"]))
    if out.get("status") == "GRADED":
        out["units"] = round(profit_on_win(1.0, pk["price"]), 4) if out["ats"] == "W" else (-1.0 if out["ats"] == "L" else 0.0)
    return out


def summary(rows):
    g = [r for r in rows if r["status"] == "GRADED"]
    w, l, p = (sum(r["ats"] == x for r in g) for x in "WLP")
    units = round(math.fsum(r["units"] for r in g), 4)
    return {"selections": len(rows), "graded": len(g), "wins": w, "losses": l, "pushes": p, "units": units,
            "roi_pct": round(100 * units / len(g), 2) if g else None,
            "pending": sum(r["status"] == "AWAITING_RESULT" for r in rows),
            "warning": "Fewer than 100 graded selections: results are dominated by chance." if len(g) < 100 else ""}


def report(decisions, results):
    """Official selections and the watchlist graded separately (never mixed with C1-C4), per sport and engine."""
    out = {}
    for cls in ("OFFICIAL", "WATCHLIST"):
        rows = [grade(d, results.get((d["sport"], d["event_id"]))) for d in decisions if d["classification"] == cls]
        rows.sort(key=lambda r: (r["kickoff"], r["sport"]), reverse=True)
        by_sport = {sp: summary([r for r in rows if r["sport"] == sp]) for sp in ("nfl", "cfb")}
        out[cls.lower()] = {"overall": summary(rows), "by_sport": by_sport, "rows": rows}
    out["passes_sealed"] = sum(d["classification"] == "PASS" for d in decisions)
    out["data_quality_issues"] = [{k: d.get(k) for k in ("sport", "event_id", "home", "away", "kickoff", "classification",
                                                          "data_quality")}
                                  for d in decisions if d.get("data_quality", "VALID") != "VALID"]
    return out


def report_all(decisions, results):
    """One report per strategy (official_needs.v1, spread.v2, total.v1): never mixed."""
    by = {}
    for d in decisions:
        by.setdefault(strategy_of(d), []).append(d)
    out = {}
    for st, ds in sorted(by.items()):
        r = report(ds, results)
        off = [d for d in ds if d["classification"] == "OFFICIAL"]
        r["tiers"] = {"PERFECT": sum(d.get("tier") == "PERFECT" for d in off),
                      "STANDARD": sum(d.get("tier") == "STANDARD" for d in off)}
        out[st] = r
    return out
