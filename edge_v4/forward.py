"""
edge_v4/forward.py -- forward (paper) scan: every fresh book price vs the
sharp no-vig consensus, at time T, for the EV Opportunity Scanner.

Probability source: the market consensus itself (Engine C adds no
measurable information beyond near-close lines -- see ENGINE_C_REPORT.md),
re-priced to each book's own number with Engine C's key-number
distribution. An opportunity therefore means "this book's price is better
than the sharp market's fair price", which is the realistic retail edge.
Engine B evidence is attached for context but never upgrades a decision.
A strategy not marked validated in the registry is capped at WATCH.
"""

import time

from edge_v4 import ev, ledger, pricing
from edge_v4.fairline import SpreadFair, TotalFair
from market_v4 import asof
from sharp_v4 import signals

STRATEGY, VERSION = "S1_consensus_line_shop", "1"
DESCRIPTION = ("Bet a book's price when it beats the sharp-weighted no-vig consensus by the policy's "
               "uncertainty-adjusted threshold; fair prices at other numbers via Engine C key-number distribution")
MARKET_SIDES = {"spreads": ("home", "away"), "totals": ("over", "under"), "h2h": ("home", "away")}


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _consensus_se(state, market):
    """Ex-ante uncertainty of the consensus probability: dispersion of fresh
    books' no-vig probabilities on the main line / sqrt(n), floored."""
    a, b = asof.SIDES[market]
    vals = []
    for name in state.get("consensus_books", []):
        bk = state["books"][name]
        if bk.get(a) and bk.get(b):
            try:
                vals.append(pricing.devig([bk[a][0], bk[b][0]])[0])
            except ValueError:
                pass
    if len(vals) < 2:
        return 0.02
    m = sum(vals) / len(vals)
    sd = (sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5
    return max(0.005, sd / len(vals) ** 0.5)


def scan(market_conn, ledger_conn, events, T, spread_kn, spread_sigma, total_kn, total_sigma,
         policy=ev.DecisionPolicy(), max_age_s=asof.DEFAULT_MAX_AGE_S, record=True, allow_demo=False):
    """events: [{'event_id', 'kickoff' (epoch), 'home', 'away'}]. Returns decision rows."""
    validated = False
    if ledger_conn is not None:
        ledger.register(ledger_conn, STRATEGY, VERSION, DESCRIPTION, {"policy": policy.__dict__, "max_age_s": max_age_s},
                        "2026-10-26T00:00:00Z")
        validated = bool(ledger_conn.execute("SELECT validated FROM v4e_strategy WHERE strategy_id=? AND version=?",
                                             (STRATEGY, VERSION)).fetchone()[0])
    record = record and ledger_conn is not None
    rows = []
    for e in events:
        if T >= e["kickoff"]:
            continue
        for market, (a, b) in MARKET_SIDES.items():
            st = asof.market_state(market_conn, e["event_id"], market, T, max_age_s)
            if st["status"] != "OK":
                rows.append({"game_id": e["event_id"], "market": market, "selection": None, "decision": ev.NO_BET,
                             "reasons": [f"market {st['status']}"], "home": e["home"], "away": e["away"]})
                continue
            try:
                sh = signals.analyse(market_conn, e["event_id"], market, T, allow_demo=allow_demo)
            except signals.SyntheticSourceRejected:
                sh = None
            se = _consensus_se(st, market)
            fair = None
            if market == "spreads":
                fair = SpreadFair(spread_kn, spread_sigma, st["line"], st["fair"]["home"])
            elif market == "totals":
                fair = TotalFair(total_kn, total_sigma, st["line"], st["fair"]["over"])
            for book, q in st["books"].items():
                if not q["fresh"]:
                    continue
                for side in (a, b):
                    if not q.get(side):
                        continue
                    price, line = q[side]
                    if fair is not None:
                        pw, pu = fair.side_probs(side, line)
                    else:
                        pw, pu = st["fair"][side], 0.0          # h2h: ties treated as no action (push mass ~0.3%)
                    support, conflict = [], []
                    if sh and sh["status"] == "SIGNAL":
                        (support if sh["suspected_side"] == side else conflict).append(
                            f"Engine B (experimental) suspects {sh['suspected_side']} (score {sh['score']})")
                    cand = ev.Candidate(game_id=e["event_id"], market=market, selection=side, line=line, book=book,
                                        american=int(price), price_age_s=q["age_s"], p_win_model=pw, p_push_model=pu,
                                        p_win_se=se, p_win_market=pw, p_push_market=pu, supporting=support,
                                        conflicting=conflict, validated_strategy=validated, observed_at=_iso(T))
                    row = ev.evaluate(cand, policy)
                    row.update(home=e["home"], away=e["away"], consensus_line=st["line"],
                               sharp_books=st["sharp_books_on_line"], kickoff=_iso(e["kickoff"]))
                    if record:
                        ledger.record_decision(ledger_conn, STRATEGY, VERSION, "forward", row, _iso(T),
                                               _iso(e["kickoff"]))
                    rows.append(row)
    if record:
        ledger_conn.commit()
    order = {ev.BET: 0, ev.WATCH: 1, ev.NO_BET: 2}
    rows.sort(key=lambda r: (order[r["decision"]], -(r.get("ev_interval") or (0, 0))[0]))
    return rows
