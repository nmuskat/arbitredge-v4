"""
market_intel/sharp.py -- ARBITREDGE MARKET NEED x SYNTHETIC SHARP AGREEMENT (research; read-only on the ledger).

Reads the existing synthetic-book wagers (archetype SHARP) and NEVER creates or changes a bettor or a wager.
Thresholds FIXED on 2026-11-12 before any agreement outcome was computed (constants below).

Point in time:
  * only wagers ACCEPTED at or before the cutoff;
  * historical (genuine) mode additionally requires the wager's ledger file to have been PUBLISHED (release upload
    time) at or before the cutoff -- positions written later (catch-up advances) are excluded, never back-filled;
  * when several runs cover one game (superseded engine versions), the run whose first file was published latest
    by the cutoff is used (the one the dashboard showed at that time).

SIMULATED: synthetic sharp tickets/handle are model output, never real sportsbook money.
"""
import collections

MIN_WAGERS = 3            # fewer eligible sharp wagers -> NO SHARP CONSENSUS (sparse)
MIN_BETTORS = 2           # fewer distinct sharp bettors -> NO SHARP CONSENSUS (sparse)
STRONG_HANDLE = 0.65      # strong: >= 65% of sharp handle ...
STRONG_TICKETS = 0.60     # ... and >= 60% of sharp tickets on the same side ...
STRONG_ROBUST = 0.55      # ... and still >= 55% of handle without the single largest sharp wager
LEAN_HANDLE = 0.55        # lean: >= 55% of sharp handle, ticket majority not on the other side
SIDES = {"spread": ("home", "away"), "total": ("over", "under")}

CATEGORIES = ("STRONG_AGREEMENT", "MODERATE_AGREEMENT", "DISAGREEMENT", "NO_SHARP_CONSENSUS", "MARKET_NEED_ONLY",
              "SHARP_ONLY", "NO_SIGNAL")


def position(wagers, market):
    """wagers: eligible SHARP wagers of one game-market. Returns the sharp position summary."""
    a, b = SIDES[market]
    n = len(wagers)
    if n == 0:
        return {"status": "NO_SHARP_WAGERS", "tickets": 0, "direction": None}
    t = collections.Counter(w["side"] for w in wagers)
    h = collections.defaultdict(float)
    for w in wagers:
        h[w["side"]] += w["stake"]
    tot = sum(h.values())
    big = max(wagers, key=lambda w: w["stake"])
    h_wo = dict(h)
    h_wo[big["side"]] -= big["stake"]
    tot_wo = tot - big["stake"]
    bettors = len({w["bettor_uid"] for w in wagers})
    out = {"tickets": n, "bettors": bettors,
           "ticket_pct": {s: t[s] / n for s in (a, b)}, "handle_pct": {s: (h[s] / tot if tot else 0.0) for s in (a, b)},
           "handle": {s: round(h[s], 2) for s in (a, b)},
           "largest_wager_share": big["stake"] / tot if tot else None,
           "first_at": min(w["accepted_at"] for w in wagers), "last_at": max(w["accepted_at"] for w in wagers),
           "avg_price": {s: (sum(w["accepted_price"] for w in wagers if w["side"] == s) / t[s]) if t[s] else None
                         for s in (a, b)}}
    if n < MIN_WAGERS or bettors < MIN_BETTORS:
        return dict(out, status="SPARSE", direction=None, strength=None)
    hs = max((a, b), key=lambda s: out["handle_pct"][s])
    ts = max((a, b), key=lambda s: out["ticket_pct"][s])
    if out["ticket_pct"][a] == out["ticket_pct"][b]:
        ts = None
    hp, tp = out["handle_pct"][hs], out["ticket_pct"][hs]
    robust = (h_wo[hs] / tot_wo) if tot_wo > 0 else 0.0
    if ts is not None and ts != hs and out["ticket_pct"][ts] > 0.5 and hp >= LEAN_HANDLE:
        return dict(out, status="TICKET_HANDLE_CONFLICT", direction=None, strength=None,
                    note=f"handle majority {hs}, ticket majority {ts}")
    if hp >= STRONG_HANDLE and tp >= STRONG_TICKETS and robust >= STRONG_ROBUST:
        return dict(out, status="DIRECTIONAL", direction=hs, strength="STRONG", robust_handle_pct=robust)
    if hp >= LEAN_HANDLE and tp >= 0.5:
        return dict(out, status="DIRECTIONAL", direction=hs, strength="LEAN", robust_handle_pct=robust)
    return dict(out, status="BALANCED", direction=None, strength=None, robust_handle_pct=robust)


def classify(need_side, pos):
    """need_side: market-derived NEED side or None (PASS / insufficient)."""
    d = pos.get("direction")
    if pos.get("status") == "NO_SHARP_WAGERS":
        return ("MARKET_NEED_ONLY", "NEED exists; no eligible synthetic sharp wagers") if need_side else \
            ("NO_SIGNAL", "no NEED and no synthetic sharp wagers")
    if need_side is None:
        return ("SHARP_ONLY", f"no NEED; synthetic sharps lean {d} ({pos['strength']})") if d else \
            ("NO_SIGNAL", "no NEED; synthetic sharps " + pos["status"].lower().replace("_", " "))
    if d is None:
        return "NO_SHARP_CONSENSUS", "synthetic sharps " + pos["status"].lower().replace("_", " ")
    if d == need_side:
        return (("STRONG_AGREEMENT" if pos["strength"] == "STRONG" else "MODERATE_AGREEMENT"),
                f"NEED {need_side}; synthetic sharps {pos['handle_pct'][d]:.0%} of handle / "
                f"{pos['ticket_pct'][d]:.0%} of tickets on {d}")
    return "DISAGREEMENT", f"NEED {need_side}; synthetic sharps lean {d} ({pos['strength']})"


def eligible(wagers, sport, event_id, market, cutoff_iso, published_by=None, run_choice=None):
    """Sharp wagers of one game-market accepted by the cutoff (ISO strings compare chronologically).
    published_by: {run_id: [(file_published_iso, wager_ids)]} -> genuine mode (file published <= cutoff).
    run_choice: run_id to use for this game (latest-published run by the cutoff), or None for all."""
    out = []
    for w in wagers:
        if (w["archetype"] != "SHARP" or w["sport"] != sport or w["canonical_game_id"] != event_id
                or w["market"] != market or w["accepted_at"] > cutoff_iso):
            continue
        if run_choice is not None and w["run_id"] != run_choice:
            continue
        if published_by is not None and w.get("_published_at", "9999") > cutoff_iso:
            continue
        out.append(w)
    return out
