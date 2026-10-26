"""
sportsbook_v4/grading.py -- historical and prospective pick grading (Milestone 2).

What it measures, per pick:
  * ATS result at the pick's OWN recorded line (win / loss / push)
  * profit in units at the pick's recorded price (1 unit risked; push = 0)
  * closing-line value against the observed consensus close
      - points CLV  = pick line - closing line for the same side
      - price CLV   = implied(close price) - implied(pick price), ONLY when the
                      pick line equals the closing line (otherwise not computed)
      - no-vig CLV  = devigged close probability - implied(pick price), same-line only
and, in aggregate: W-L-P, win % ex-push with Wilson interval, exact binomial
p-value vs 50%, ROI, break-even win %, CLV summaries, calibration when a
forecast probability was recorded, grouped by sport / tier / market
condition / signal agreement / price provenance.

WHAT IT REFUSES TO DO (no invention):
  * a pick with no side, no line, or no decision timestamp is UNGRADABLE --
    it is never assigned a default line or time;
  * a pick with no price is graded ATS but EXCLUDED from ROI -- no default -110;
  * a pick timestamped at/after kickoff is NOT_PROSPECTIVE and excluded;
  * a result is used only if is_final and retrieved at/before the grading
    cutoff and after kickoff; score revisions are flagged;
  * prices DERIVED from quotes that were recorded with a pick (e.g. the
    per-book lines in experimental_mixed_signals.full_record_json) are kept
    separate from RECORDED prices -- ROI is never reported over a mix.
"""

import csv
import json
import math
from dataclasses import asdict, dataclass, field

from sportsbook_v4.market import implied_probability, profit_on_win, representative_offer
from sportsbook_v4.settlement import book_pnl_total, exposure
from sportsbook_v4.util import to_epoch

GRADER_VERSION = "v4_grader_m2"
MIN_SAMPLE_FOR_CLAIMS = 300
PROVENANCES = ("RECORDED", "DERIVED_FROM_RECORDED_QUOTES", "MISSING")


@dataclass(frozen=True)
class PickRecord:
    pick_id: str
    source: str
    source_mutable: bool
    canonical_game_id: str
    sport: str
    side: str | None                 # 'home' | 'away'
    line: float | None               # signed line for the side taken
    price: float | None              # American odds
    price_provenance: str            # one of PROVENANCES
    decided_at: str | None
    tier: str | None = None
    market_condition: str | None = None
    signals_agree: bool | None = None
    forecast_prob: float | None = None
    notes: tuple = field(default_factory=tuple)


def audit_pick(pick, kickoff):
    issues = []
    if pick.side not in ("home", "away"):
        issues.append("MISSING_OR_INVALID_SIDE")
    if pick.line is None:
        issues.append("MISSING_LINE")
    if pick.decided_at is None:
        issues.append("MISSING_TIMESTAMP")
    elif kickoff is not None and to_epoch(pick.decided_at) >= to_epoch(kickoff):
        issues.append("NOT_PROSPECTIVE")
    if kickoff is None:
        issues.append("UNKNOWN_GAME")
    if pick.price is None:
        issues.append("MISSING_PRICE")
    else:
        try:
            implied_probability(pick.price)
        except ValueError:
            issues.append("INVALID_PRICE")
    if pick.price_provenance not in PROVENANCES:
        issues.append("UNKNOWN_PRICE_PROVENANCE")
    if pick.source_mutable:
        issues.append("SOURCE_MUTABLE")
    return issues


# ------------------------------------------------------------------ results

def select_final_result(observations, kickoff, grade_as_of):
    """
    observations: dicts with retrieved_at, is_final, final_score_home, final_score_away.
    Returns (result_or_None, flags).
    """
    flags = []
    ko, cutoff = to_epoch(kickoff), to_epoch(grade_as_of)
    finals = []
    for o in observations:
        if o.get("is_final") != 1 or o.get("final_score_home") is None or o.get("final_score_away") is None:
            continue
        t = to_epoch(o["retrieved_at"])
        if t > cutoff:
            flags.append("FINAL_AFTER_GRADING_CUTOFF_IGNORED")
            continue
        if t < ko:
            flags.append("FINAL_BEFORE_KICKOFF_IGNORED")
            continue
        finals.append(o)
    if not finals:
        return None, sorted(set(flags)) + ["NO_FINAL_RESULT"]
    scores = {(o["final_score_home"], o["final_score_away"]) for o in finals}
    if len(scores) > 1:
        flags.append("SCORE_REVISED")
    latest = max(finals, key=lambda o: (to_epoch(o["retrieved_at"]), str(o.get("result_id", ""))))
    return {"home_score": latest["final_score_home"], "away_score": latest["final_score_away"],
            "retrieved_at": latest["retrieved_at"]}, sorted(set(flags))


def closing_line(market, game_id, kickoff):
    """Consensus (lower-median) number strictly before kickoff, from observed quotes only."""
    offer = representative_offer(market.latest_quotes(game_id, to_epoch(kickoff) - 1))
    return offer


# ------------------------------------------------------------------ grading

def ats(side, line, home_score, away_score):
    margin = (home_score - away_score) if side == "home" else (away_score - home_score)
    x = margin + line
    return "W" if x > 0 else ("P" if x == 0 else "L")


def grade_pick(pick, kickoff, result, close):
    issues = audit_pick(pick, kickoff)
    out = {"pick": asdict(pick), "kickoff": kickoff, "issues": issues, "grader_version": GRADER_VERSION}
    if {"MISSING_OR_INVALID_SIDE", "MISSING_LINE", "MISSING_TIMESTAMP", "UNKNOWN_GAME"} & set(issues):
        out["status"] = "UNGRADABLE"
        return out
    if "NOT_PROSPECTIVE" in issues:
        out["status"] = "NOT_PROSPECTIVE"
        return out
    if result is None:
        out["status"] = "NO_FINAL_RESULT"
        return out
    out["status"] = "GRADED"
    res = ats(pick.side, pick.line, result["home_score"], result["away_score"])
    out["ats"] = res
    priced = pick.price is not None and "INVALID_PRICE" not in issues
    if priced:
        out["units"] = (profit_on_win(1.0, pick.price) if res == "W" else (-1.0 if res == "L" else 0.0))
    out.update(_clv(pick, close))
    return out


def _clv(pick, close):
    if not close:
        return {"clv_available": False}
    hs = close["home_spread"]
    close_line = hs if pick.side == "home" else -hs
    out = {"clv_available": True, "closing_line": close_line, "clv_points": pick.line - close_line,
           "closing_books": close["book_count"]}
    side_price = close["home_price"] if pick.side == "home" else close["away_price"]
    out["closing_price"] = side_price
    if pick.price is not None and side_price is not None and pick.line == close_line:
        out["clv_price_prob"] = implied_probability(side_price) - implied_probability(pick.price)
        if close["home_price"] is not None and close["away_price"] is not None:
            ih, ia = implied_probability(close["home_price"]), implied_probability(close["away_price"])
            fair = (ih if pick.side == "home" else ia) / (ih + ia)
            out["clv_novig_prob"] = fair - implied_probability(pick.price)
    return out


# --------------------------------------------------------------- aggregates

def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [c - h, c + h]


def binom_two_sided(k, n):
    if n == 0:
        return None
    probs = [math.comb(n, i) / 2 ** n for i in range(n + 1)]
    return min(1.0, sum(p for p in probs if p <= probs[k] * (1 + 1e-12)))


def summarize(graded):
    g = [x for x in graded if x["status"] == "GRADED"]
    w = sum(x["ats"] == "W" for x in g)
    loss = sum(x["ats"] == "L" for x in g)
    p = sum(x["ats"] == "P" for x in g)
    decided = w + loss
    out = {
        "picks_total": len(graded),
        "status_counts": {s: sum(x["status"] == s for x in graded) for s in
                          ("GRADED", "UNGRADABLE", "NOT_PROSPECTIVE", "NO_FINAL_RESULT")},
        "record": f"{w}-{loss}-{p}", "wins": w, "losses": loss, "pushes": p,
        "win_pct_ex_push": w / decided if decided else None,
        "win_pct_95ci": wilson(w, decided),
        "p_value_vs_50pct": binom_two_sided(w, decided),
        "sample_warning": None if decided >= MIN_SAMPLE_FOR_CLAIMS else
        f"INSUFFICIENT_SAMPLE ({decided} decided < {MIN_SAMPLE_FOR_CLAIMS}): no performance claim supported",
    }
    for prov in ("RECORDED", "DERIVED_FROM_RECORDED_QUOTES"):
        priced = [x for x in g if "units" in x and x["pick"]["price_provenance"] == prov]
        key = "roi_recorded_odds" if prov == "RECORDED" else "roi_derived_odds"
        if priced:
            units = math.fsum(x["units"] for x in priced)
            mean_imp = sum(implied_probability(x["pick"]["price"]) for x in priced) / len(priced)
            out[key] = {"n": len(priced), "units": units, "roi": units / len(priced),
                        "breakeven_win_pct_at_mean_price": mean_imp}
        else:
            out[key] = {"n": 0}
    out["excluded_from_roi_missing_price"] = sum(1 for x in g if "units" not in x)
    clv = [x for x in g if x.get("clv_available")]
    out["clv"] = {
        "n": len(clv),
        "mean_points": (sum(x["clv_points"] for x in clv) / len(clv)) if clv else None,
        "pct_positive_points": (sum(x["clv_points"] > 0 for x in clv) / len(clv)) if clv else None,
        "pct_zero_points": (sum(x["clv_points"] == 0 for x in clv) / len(clv)) if clv else None,
        "same_line_price_n": sum("clv_price_prob" in x for x in clv),
        "mean_price_clv_prob": (sum(x["clv_price_prob"] for x in clv if "clv_price_prob" in x)
                                / max(1, sum("clv_price_prob" in x for x in clv))) if clv else None,
    }
    fc = [x for x in g if x["pick"]["forecast_prob"] is not None and x["ats"] != "P"]
    if fc:
        brier = sum((x["pick"]["forecast_prob"] - (x["ats"] == "W")) ** 2 for x in fc) / len(fc)
        bins = {}
        for x in fc:
            b = min(9, int(x["pick"]["forecast_prob"] * 10))
            bins.setdefault(b, []).append(x)
        out["calibration"] = {"n": len(fc), "brier": brier, "bins": {
            f"{b / 10:.1f}-{(b + 1) / 10:.1f}": {"n": len(v), "mean_forecast": sum(y["pick"]["forecast_prob"] for y in v) / len(v),
                                                 "win_rate": sum(y["ats"] == "W" for y in v) / len(v)}
            for b, v in sorted(bins.items())}}
    else:
        out["calibration"] = "NO_FORECAST_PROBABILITIES_RECORDED"
    return out


def report(graded):
    groups = {}
    for key in ("sport", "tier", "market_condition", "signals_agree", "price_provenance", "source"):
        buckets = {}
        for x in graded:
            buckets.setdefault(str(x["pick"][key]), []).append(x)
        groups[key] = {k: summarize(v) for k, v in sorted(buckets.items())}
    return {"grader_version": GRADER_VERSION, "overall": summarize(graded), "by": groups}


# ---------------------------------------------------------------- importers

def reconstruction_audit(picks, kickoffs):
    """What fraction of a historical pick set can be graded, and with what caveats."""
    n = len(picks)
    issues = [audit_pick(p, kickoffs.get(p.canonical_game_id)) for p in picks]
    count = lambda tag: sum(tag in i for i in issues)  # noqa: E731
    out = {
        "picks": n,
        "missing_side": count("MISSING_OR_INVALID_SIDE"), "missing_line": count("MISSING_LINE"),
        "missing_timestamp": count("MISSING_TIMESTAMP"), "unknown_game": count("UNKNOWN_GAME"),
        "not_prospective": count("NOT_PROSPECTIVE"), "missing_price": count("MISSING_PRICE"),
        "price_provenance": {pv: sum(p.price_provenance == pv for p in picks) for pv in PROVENANCES},
        "from_mutable_source": count("SOURCE_MUTABLE"),
    }
    blocking = out["missing_side"] + out["missing_line"] + out["missing_timestamp"] + out["unknown_game"]
    if n == 0:
        out["verdict"] = "NO_PICKS_FOUND"
    elif blocking == n:
        out["verdict"] = "NOT_RECONSTRUCTABLE"
    elif blocking or out["missing_price"] or out["from_mutable_source"] or out["price_provenance"]["RECORDED"] < n:
        out["verdict"] = "PARTIALLY_RECONSTRUCTABLE"
    else:
        out["verdict"] = "RECONSTRUCTABLE"
    return out


def _lower_median_price(prices):
    cands = sorted((implied_probability(p), p) for p in prices if p is not None and not (-100 < p < 100))
    if not cands:
        return None
    n = len(cands)
    return cands[n // 2][1] if n % 2 else cands[n // 2 - 1][1]


def picks_from_experimental_mixed_signals(conn):
    """
    READ-ONLY adapter for the legacy experimental_mixed_signals table (column
    names taken from operations/run_collection.py's INSERT; to be checked
    against the real `.schema`). Each row is the LAST pre-kickoff state of a
    mutable record (the writer upserts/deletes inside T-15m), so every pick
    is flagged SOURCE_MUTABLE. The table stores no single price: when the
    per-book lines saved in full_record_json include prices at the recorded
    representative spread, their lower-median implied price is used and
    labelled DERIVED_FROM_RECORDED_QUOTES; otherwise price is MISSING.
    """
    rows = conn.execute(
        "SELECT canonical_game_id, sport, signal_version, as_of, written_at, arbitrage_side, "
        "signal_line, data_quality, aggregate_regime, full_record_json FROM experimental_mixed_signals "
        "ORDER BY canonical_game_id, signal_version"
    ).fetchall()
    picks = []
    for gid, sport, ver, as_of, written_at, side, line, dq, regime, frj in rows:
        price, prov, notes = None, "MISSING", [f"signal_version={ver}", f"written_at={written_at}"]
        try:
            lines = ((json.loads(frj) or {}).get("signal_line") or {}).get("lines") or []
        except (TypeError, ValueError):
            lines, notes = [], notes + ["full_record_json_unparseable"]
        if line is not None:
            matching = [ln.get("price") for ln in lines if ln.get("spread") is not None
                        and float(ln["spread"]) == float(line)]
            price = _lower_median_price(matching)
            if price is not None:
                prov = "DERIVED_FROM_RECORDED_QUOTES"
        picks.append(PickRecord(
            pick_id=f"ems:{gid}:{ver}", source="experimental_mixed_signals", source_mutable=True,
            canonical_game_id=str(gid), sport=sport, side=side, line=None if line is None else float(line),
            price=price, price_provenance=prov, decided_at=as_of, tier=dq, market_condition=regime,
            notes=tuple(notes),
        ))
    return picks


REQUIRED_CSV_COLUMNS = ("pick_id", "canonical_game_id", "sport", "side", "line", "price", "decided_at")


def picks_from_csv(path, source="manual_ledger", source_mutable=True):
    """
    A hand-kept pick ledger (e.g. whatever produced 37-34-5). Required columns
    must exist; empty cells stay None -- nothing is filled in. A spreadsheet is
    treated as mutable unless the caller can show otherwise.
    """
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_CSV_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"pick ledger is missing required columns: {missing}")
        picks = []
        for row in reader:
            def val(k, cast=str):
                v = (row.get(k) or "").strip()
                return cast(v) if v else None
            price = val("price", float)
            agree = val("signals_agree")
            picks.append(PickRecord(
                pick_id=val("pick_id"), source=source, source_mutable=source_mutable,
                canonical_game_id=val("canonical_game_id"), sport=val("sport"), side=val("side"),
                line=val("line", float), price=price, price_provenance="RECORDED" if price is not None else "MISSING",
                decided_at=val("decided_at"), tier=val("tier"), market_condition=val("market_condition"),
                signals_agree=None if agree is None else agree.lower() in ("1", "true", "yes"),
                forecast_prob=val("forecast_prob", float),
            ))
    return picks


def picks_from_v4_decisions(conn):
    """Official V4 plays (PLAY rows only; the active policy produces none)."""
    rows = conn.execute(
        "SELECT decision_id, canonical_game_id, sport, decided_at, play_side, play_line, play_price, "
        "decision_point FROM v4_prospective_decision WHERE official_play='PLAY'"
    ).fetchall()
    return [PickRecord(pick_id=f"v4:{d}", source="v4_prospective_decision", source_mutable=False,
                       canonical_game_id=g, sport=s, side=side, line=line, price=price,
                       price_provenance="RECORDED" if price is not None else "MISSING",
                       decided_at=t, tier=dp)
            for d, g, s, t, side, line, price, dp in rows]


def results_from_db(conn, game_ids):
    """READ-ONLY: every game_results observation for the given games."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(game_results)")}
    needed = {"canonical_game_id", "is_final", "final_score_home", "final_score_away", "retrieved_at"}
    if not needed <= cols:
        raise ValueError(f"game_results lacks columns {sorted(needed - cols)}")
    rid = "result_id" if "result_id" in cols else "NULL"
    out = {}
    for gid in game_ids:
        rows = conn.execute(
            f"SELECT {rid}, is_final, final_score_home, final_score_away, retrieved_at FROM game_results "
            "WHERE canonical_game_id = ?", (gid,)).fetchall()
        out[gid] = [{"result_id": r[0], "is_final": r[1], "final_score_home": r[2],
                     "final_score_away": r[3], "retrieved_at": r[4]} for r in rows]
    return out


# ---------------------------------------------------------- Need accuracy

def need_outcome(need_side, wagers, result, close):
    """
    How a financial Need fared: did the Need side cover the CLOSING number,
    and what did the synthetic book actually make at the real final margin
    (every accepted wager settled at its own number)? A Need is not a bet;
    this measures exposure accuracy, not betting profit.
    """
    if need_side not in ("home", "away") or result is None:
        return {"status": "NOT_EVALUABLE"}
    margin = result["home_score"] - result["away_score"]
    e = exposure(wagers)
    out = {"status": "EVALUATED", "final_home_margin": margin,
           "realized_book_pnl": book_pnl_total(wagers, margin),
           "pnl_if_need_side_covers_all": e["pnl_if_home_covers_all" if need_side == "home" else "pnl_if_away_covers_all"],
           "worst_case_pnl": e.get("worst_case_pnl")}
    if close:
        line = close["home_spread"] if need_side == "home" else -close["home_spread"]
        out["need_side_vs_close"] = ats(need_side, line, result["home_score"], result["away_score"])
        out["closing_line_for_need_side"] = line
    return out
