"""
sportsbook_v4/bettor_settlement.py -- settle every simulated wager from real final scores, and derive each
simulated bettor's bankroll (MODELED; docs_v4/SETTLEMENT_BANKROLL_PLAN.md phases 2 and 4).

Rules:
  * Accepted wagers are never touched. Settlement is a separate append-only record that refers to a wager by
    id, stored in immutable hash-verified files (release v4-settlement).
  * A wager is settled only when its game has a final score from its source (data_v4.results.match). No score,
    an unmatched or ambiguous game: the wager stays OPEN. Nothing is guessed; games are never voided
    automatically (postponements are not in the data), they stay OPEN and are reported as overdue.
  * Each wager is graded at ITS OWN accepted line and price (settlement.wager_outcome): spread, total and
    moneyline; a moneyline tie is a push (no action). Money is in integer CENTS: a winning payout is the
    stake plus the price's profit rounded to the cent, as a book pays it.
  * A later, different final score (a source correction) creates a NEW settlement row with `supersedes`
    pointing at the old one; the newest row per wager is authoritative and history is never lost.
  * Bankrolls are derived, never stored as a mutable balance: opening bankroll + settled P&L - open stakes.
    Runs superseded by a newer model version are settled for the record but excluded from bankrolls (the
    same simulated week would otherwise count twice).
"""

import math

from sportsbook_v4.market import profit_on_win
from sportsbook_v4.params import BOOK
from sportsbook_v4.settlement import wager_outcome
from sportsbook_v4.util import json_hash

RULES_VERSION = "settle_v1"
OVERDUE_S = 48 * 3600


def cents(x):
    return int(round(x * 100))


def amounts(result, stake, price):
    """(payout_cents returned to the bettor, bettor pnl_cents)."""
    s = cents(stake)
    if result == "win":
        win = cents(profit_on_win(stake, price))
        return s + win, win
    if result == "push":
        return s, 0
    return 0, -s


def settle_one(w, res, settled_at, supersedes=None):
    result = wager_outcome(w, res["home_score"], res["away_score"])
    payout, pnl = amounts(result, w["stake"], w["accepted_price"])
    rec = {"rules_version": RULES_VERSION, "wager_id": w["wager_id"], "run_id": w["run_id"],
           "population_id": w["population_id"], "bettor_index": w["bettor_index"], "archetype": w["archetype"],
           "sport": w["sport"], "event_id": w["canonical_game_id"], "market": w["market"], "side": w["side"],
           "accepted_line": w["accepted_line"], "accepted_price": w["accepted_price"], "stake_cents": cents(w["stake"]),
           "home_score": res["home_score"], "away_score": res["away_score"],
           "result_ref": {"source": res["source"], "source_id": res["source_id"], "fetched_at": res["fetched_at"],
                          "corrected": bool(res.get("corrected"))},
           "result": result, "payout_cents": payout, "pnl_cents": pnl, "settled_at": settled_at,
           "supersedes": supersedes}
    rec["settlement_id"] = json_hash([w["wager_id"], res["home_score"], res["away_score"], RULES_VERSION])[:40]
    rec["record_hash"] = json_hash(rec)
    return rec


def latest(settlements):
    """{wager_id: authoritative settlement} -- the row nothing supersedes. Raises on a broken chain."""
    by_id = {s["settlement_id"]: s for s in settlements}
    superseded = {s["supersedes"] for s in settlements if s["supersedes"]}
    out = {}
    for s in settlements:
        if s["settlement_id"] in superseded:
            continue
        if s["supersedes"] and s["supersedes"] not in by_id:
            raise ValueError(f"settlement {s['settlement_id']} supersedes an unknown row")
        if s["wager_id"] in out:
            raise ValueError(f"wager {s['wager_id']} has two authoritative settlements")
        out[s["wager_id"]] = s
    return out


def new_settlements(wagers, results, existing, settled_at):
    """Rows to append: first settlements, and corrections when the final score changed."""
    cur = latest(existing)
    out = []
    for w in wagers:
        res = results.get((w["sport"], w["canonical_game_id"]))
        if res is None:
            continue
        old = cur.get(w["wager_id"])
        if old is not None and (old["home_score"], old["away_score"]) == (res["home_score"], res["away_score"]):
            continue
        out.append(settle_one(w, res, settled_at, supersedes=old["settlement_id"] if old else None))
    return out


def verify(settlements, wagers_by_id):
    """Every settlement refers to an accepted wager and re-grades identically from its stored score."""
    problems = []
    for s in settlements:
        w = wagers_by_id.get(s["wager_id"])
        if w is None:
            problems.append(f"{s['settlement_id']}: unknown wager {s['wager_id']}")
            continue
        r = wager_outcome(w, s["home_score"], s["away_score"])
        p, n = amounts(r, w["stake"], w["accepted_price"])
        if (r, p, n, cents(w["stake"])) != (s["result"], s["payout_cents"], s["pnl_cents"], s["stake_cents"]):
            problems.append(f"{s['settlement_id']}: does not re-grade identically")
    try:
        latest(settlements)
    except ValueError as e:
        problems.append(str(e))
    return problems


def bankrolls(bettors, wagers, settlements, active_runs, now, kickoffs):
    """Per-sport bankroll summary plus per-bettor rows, all in cents, from the event stream.
    bettors: [{population_id, bettor_index, archetype, bankroll}]; kickoffs: {(sport, event_id): epoch}."""
    cur = latest(settlements)
    acc = {}
    for b in bettors:
        acc[(b["population_id"], b["bettor_index"])] = {
            "population_id": b["population_id"], "bettor_index": b["bettor_index"], "archetype": b["archetype"],
            "opening_cents": cents(b["bankroll"]), "settled_pnl_cents": 0, "open_stake_cents": 0,
            "settled_wagers": 0, "open_wagers": 0, "w": 0, "l": 0, "p": 0}
    overdue = 0
    for w in wagers:
        if w["run_id"] not in active_runs:
            continue
        a = acc.get((w["population_id"], w["bettor_index"]))
        if a is None:
            continue
        s = cur.get(w["wager_id"])
        if s is None:
            a["open_stake_cents"] += cents(w["stake"])
            a["open_wagers"] += 1
            k = kickoffs.get((w["sport"], w["canonical_game_id"]))
            overdue += bool(k and now - k > OVERDUE_S)
        else:
            a["settled_pnl_cents"] += s["pnl_cents"]
            a["settled_wagers"] += 1
            a[{"win": "w", "loss": "l", "push": "p"}[s["result"]]] += 1
    for a in acc.values():
        a["available_cents"] = a["opening_cents"] + a["settled_pnl_cents"] - a["open_stake_cents"]
        a["equity_cents"] = a["opening_cents"] + a["settled_pnl_cents"]
    return list(acc.values()), overdue


def reconcile(wagers, settlements, active_runs, rows):
    """Checks, each must be exactly zero: per game, the book's realized P&L equals minus the bettors' settled P&L
    (both summed per wager in cents); per bettor, available = opening + settled - open, recomputed independently."""
    cur = latest(settlements)
    book, bettors = {}, {}
    for w in wagers:
        s = cur.get(w["wager_id"])
        if s is None or w["run_id"] not in active_runs:
            continue
        k = (w["sport"], w["canonical_game_id"])
        r = s["result"]
        book_c = cents(w["stake"]) if r == "loss" else (-cents(profit_on_win(w["stake"], w["accepted_price"]))
                                                       if r == "win" else 0)
        book[k] = book.get(k, 0) + book_c
        bettors[k] = bettors.get(k, 0) + s["pnl_cents"]
    game_diff = sum(abs(book[k] + bettors[k]) for k in book)
    open_by = {}
    for w in wagers:
        if w["run_id"] in active_runs and w["wager_id"] not in cur:
            key = (w["population_id"], w["bettor_index"])
            open_by[key] = open_by.get(key, 0) + cents(w["stake"])
    pnl_by = {}
    for s in cur.values():
        if s["run_id"] in active_runs:
            key = (s["population_id"], s["bettor_index"])
            pnl_by[key] = pnl_by.get(key, 0) + s["pnl_cents"]
    bettor_diff = sum(abs(r["available_cents"] - (r["opening_cents"] + pnl_by.get((r["population_id"], r["bettor_index"]), 0)
                                                    - open_by.get((r["population_id"], r["bettor_index"]), 0))) for r in rows)
    return {"games_settled": len(book), "book_realized_cents": sum(book.values()),
            "bettor_settled_pnl_cents": sum(bettors.values()), "game_mismatch_cents": game_diff,
            "bettor_mismatch_cents": bettor_diff, "ok": game_diff == 0 and bettor_diff == 0}


def summary(rows, wagers, settlements, active_runs):
    cur = latest(settlements)
    out = {}
    for sport in ("nfl", "cfb"):
        rs = [r for r in rows if r["population_id"].startswith(sport + "|")]
        act = [r for r in rs if r["settled_wagers"] or r["open_wagers"]]
        by_arch = {}
        for r in act:
            a = by_arch.setdefault(r["archetype"], {"bettors": 0, "up": 0, "down": 0, "even": 0, "pnl_cents": 0})
            a["bettors"] += 1
            a["pnl_cents"] += r["settled_pnl_cents"]
            a["up" if r["settled_pnl_cents"] > 0 else "down" if r["settled_pnl_cents"] < 0 else "even"] += 1
        ws = [w for w in wagers if w["sport"] == sport and w["run_id"] in active_runs]
        settled = [cur[w["wager_id"]] for w in ws if w["wager_id"] in cur]
        handle = sum(s["stake_cents"] for s in settled)
        book = -sum(s["pnl_cents"] for s in settled)
        out[sport] = {"bettors": len(rs), "bettors_with_wagers": len(act), "wagers": len(ws),
                      "settled_wagers": len(settled), "open_wagers": len(ws) - len(settled),
                      "settled_handle_cents": handle, "book_realized_pnl_cents": book,
                      "book_hold_pct": round(100.0 * book / handle, 2) if handle else None,
                      "results": {k: sum(s["result"] == k for s in settled) for k in ("win", "loss", "push")},
                      "bettors_below_min_stake": sum(1 for r in act if r["available_cents"] < cents(BOOK["min_wager"])),
                      "by_archetype": by_arch}
    return out
