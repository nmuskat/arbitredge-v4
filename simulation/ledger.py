"""
simulation/ledger.py

Phase 2A. Builds a real, monetarily-accurate sportsbook ledger from a
list of INDIVIDUAL accepted spread wagers (as produced by
simulation/population.py's run_simulation), plus the ledger-implied
"ArbitrEdge Need" -- a clean, from-first-principles alternative to the
legacy V2 synthetic_sportsbook.evaluate_synthetic_book() hand-weighted
liability_score (book_need/synthetic_sportsbook.py). That legacy score
blends exposure/handle-share/book-response components with hand-picked
weights and has no notion of actual dollars; this module computes real
P&L in dollars under each hypothetical spread-cover outcome and is not
used by, and does not feed, the legacy function.

TERMINOLOGY -- kept deliberately distinct, never interchanged:
  handle                total stake wagered on the game
  bettor profit         a winning bettor's profit (not their return)
  gross_return          total amount paid to a bettor on a win,
                         INCLUDING their returned stake
  sportsbook P&L        the book's own dollar position for one outcome:
                         -profit on each winner, +stake on each loser,
                         0 on each push
  liability / Need      which side the book would prefer, derived from
                         comparing pnl_if_home_covers vs
                         pnl_if_away_covers -- NOT the legacy 0-100
                         liability_score
  P&L swing             the dollar gap between the two cover outcomes

Every wager in `wagers` is expected to be a dict with at least:
    side ("home"|"away"), stake (float), accepted_price (float|None)
accepted_price is REQUIRED to be non-None for every wager passed in --
population.py is responsible for never emitting a wager with no
legitimate price (see offered_line.select_representative_price); this
module raises rather than silently treating a missing price as free
money for the book.
"""

from simulation.accounting import profit_and_gross_return, InvalidPriceError


def _is_whole_number(x):
    return float(x).is_integer()


def build_game_ledger(canonical_game_id, wagers, home_spread):
    """
    wagers: list of dicts, each with side/stake/accepted_price (plus
        whatever else the caller wants to carry -- unused fields are
        ignored).
    home_spread: the signed, home-relative representative line this
        game's wagers were accepted against (from
        offered_line.construct_offered_line). Used only to report
        whether a push is a real possible outcome (whole-number
        spread) -- NOT to re-derive accepted_line per wager, which
        each wager already carries individually.

    Returns a dict with handle/ticket/money composition plus the
    monetary ledger-implied Need. Raises InvalidPriceError if any
    wager carries a None/0 accepted_price -- such a wager should never
    have been produced in the first place.
    """
    home_wagers = [w for w in wagers if w["side"] == "home"]
    away_wagers = [w for w in wagers if w["side"] == "away"]

    home_handle = round(sum(w["stake"] for w in home_wagers), 2)
    away_handle = round(sum(w["stake"] for w in away_wagers), 2)
    total_handle = round(home_handle + away_handle, 2)
    home_tickets = len(home_wagers)
    away_tickets = len(away_wagers)
    total_tickets = home_tickets + away_tickets

    pnl_if_home_covers = 0.0
    pnl_if_away_covers = 0.0
    gross_return_if_home_covers = 0.0
    gross_return_if_away_covers = 0.0

    for w in home_wagers:
        if w.get("accepted_price") is None:
            raise InvalidPriceError(
                f"{canonical_game_id}: home-side wager with no accepted_price reached the ledger"
            )
        profit, gross_return = profit_and_gross_return(w["stake"], w["accepted_price"])
        # Home covers -> every home wager WINS.
        pnl_if_home_covers += -profit
        gross_return_if_home_covers += gross_return
        # Away covers -> every home wager LOSES; book keeps the stake.
        pnl_if_away_covers += w["stake"]

    for w in away_wagers:
        if w.get("accepted_price") is None:
            raise InvalidPriceError(
                f"{canonical_game_id}: away-side wager with no accepted_price reached the ledger"
            )
        profit, gross_return = profit_and_gross_return(w["stake"], w["accepted_price"])
        # Away covers -> every away wager WINS.
        pnl_if_away_covers += -profit
        gross_return_if_away_covers += gross_return
        # Home covers -> every away wager LOSES; book keeps the stake.
        pnl_if_home_covers += w["stake"]

    pnl_if_home_covers = round(pnl_if_home_covers, 2)
    pnl_if_away_covers = round(pnl_if_away_covers, 2)
    gross_return_if_home_covers = round(gross_return_if_home_covers, 2)
    gross_return_if_away_covers = round(gross_return_if_away_covers, 2)

    # A push is a REAL third outcome (stake returned, zero P&L on every
    # wager) only when the line is a whole number -- a half-point
    # spread cannot push. push_possible says whether that outcome can
    # ever actually occur at settlement; pnl_if_push is reported
    # either way since the arithmetic is trivially 0.0 regardless (all
    # stakes are simply returned), but a caller must consult
    # push_possible before treating it as a live settlement outcome.
    push_possible = home_spread is not None and _is_whole_number(home_spread)

    pnl_swing = round(abs(pnl_if_home_covers - pnl_if_away_covers), 2)

    if pnl_if_home_covers == pnl_if_away_covers:
        need_side = None
        need_tie = True
    else:
        # The book "needs" (prefers) whichever cover outcome leaves it
        # better off -- i.e. the HIGHER of its two possible P&L
        # figures. That is a statement about which outcome the book
        # would root for, not a 0-100 confidence score.
        need_side = "home" if pnl_if_home_covers > pnl_if_away_covers else "away"
        need_tie = False

    return {
        "canonical_game_id": canonical_game_id,
        "total_handle": total_handle,
        "home_handle": home_handle,
        "away_handle": away_handle,
        "total_tickets": total_tickets,
        "home_tickets": home_tickets,
        "away_tickets": away_tickets,
        "home_ticket_pct": round(100.0 * home_tickets / total_tickets, 1) if total_tickets else None,
        "away_ticket_pct": round(100.0 * away_tickets / total_tickets, 1) if total_tickets else None,
        "home_money_pct": round(100.0 * home_handle / total_handle, 1) if total_handle else None,
        "away_money_pct": round(100.0 * away_handle / total_handle, 1) if total_handle else None,
        "pnl_if_home_covers": pnl_if_home_covers,
        "pnl_if_away_covers": pnl_if_away_covers,
        "pnl_if_push": 0.0,
        "push_possible": push_possible,
        "gross_return_obligation_if_home_covers": gross_return_if_home_covers,
        "gross_return_obligation_if_away_covers": gross_return_if_away_covers,
        # R2: under a true push, every accepted stake on this game is
        # returned in full -- the book's payout obligation equals the
        # entire handle, not zero (0.0 above is the book's NET P&L
        # under a push, which is a separate statement: no money changes
        # hands on net, but money still moves out and back). Reported
        # as None (not 0.0, and not total_handle either) when the
        # offered line is a half-point and a push cannot actually
        # occur -- there is no live push obligation to report, and
        # advertising total_handle (or 0.0) for a state that can never
        # happen at settlement would be misleading.
        "gross_return_obligation_if_push": total_handle if push_possible else None,
        "pnl_swing": pnl_swing,
        "ledger_implied_need_side": need_side,
        "ledger_implied_need_tie": need_tie,
        "ledger_implied_need_magnitude": pnl_swing,
    }

def build_total_ledger(canonical_game_id, wagers, total_points):
    """
    Build the Phase 2B monetary ledger for accepted total wagers.

    Each wager must have side ("over"|"under"), stake, and a real
    accepted_price. A whole-number total can push; a half-point cannot.
    """
    over_wagers = [w for w in wagers if w["side"] == "over"]
    under_wagers = [w for w in wagers if w["side"] == "under"]

    over_handle = round(sum(w["stake"] for w in over_wagers), 2)
    under_handle = round(sum(w["stake"] for w in under_wagers), 2)
    total_handle = round(over_handle + under_handle, 2)
    over_tickets = len(over_wagers)
    under_tickets = len(under_wagers)
    total_tickets = over_tickets + under_tickets

    pnl_if_over = 0.0
    pnl_if_under = 0.0
    gross_return_if_over = 0.0
    gross_return_if_under = 0.0

    for w in over_wagers:
        if w.get("accepted_price") is None:
            raise InvalidPriceError(
                f"{canonical_game_id}: over wager with no accepted_price reached the ledger"
            )
        profit, gross_return = profit_and_gross_return(w["stake"], w["accepted_price"])
        pnl_if_over -= profit
        gross_return_if_over += gross_return
        pnl_if_under += w["stake"]

    for w in under_wagers:
        if w.get("accepted_price") is None:
            raise InvalidPriceError(
                f"{canonical_game_id}: under wager with no accepted_price reached the ledger"
            )
        profit, gross_return = profit_and_gross_return(w["stake"], w["accepted_price"])
        pnl_if_under -= profit
        gross_return_if_under += gross_return
        pnl_if_over += w["stake"]

    pnl_if_over = round(pnl_if_over, 2)
    pnl_if_under = round(pnl_if_under, 2)
    gross_return_if_over = round(gross_return_if_over, 2)
    gross_return_if_under = round(gross_return_if_under, 2)
    push_possible = total_points is not None and _is_whole_number(total_points)
    pnl_swing = round(abs(pnl_if_over - pnl_if_under), 2)

    if pnl_if_over == pnl_if_under:
        need_side = None
        need_tie = True
    else:
        need_side = "over" if pnl_if_over > pnl_if_under else "under"
        need_tie = False

    return {
        "canonical_game_id": canonical_game_id,
        "total_points": total_points,
        "total_handle": total_handle,
        "over_handle": over_handle,
        "under_handle": under_handle,
        "total_tickets": total_tickets,
        "over_tickets": over_tickets,
        "under_tickets": under_tickets,
        "over_ticket_pct": round(100.0 * over_tickets / total_tickets, 1) if total_tickets else None,
        "under_ticket_pct": round(100.0 * under_tickets / total_tickets, 1) if total_tickets else None,
        "over_money_pct": round(100.0 * over_handle / total_handle, 1) if total_handle else None,
        "under_money_pct": round(100.0 * under_handle / total_handle, 1) if total_handle else None,
        "pnl_if_over": pnl_if_over,
        "pnl_if_under": pnl_if_under,
        "pnl_if_push": 0.0,
        "push_possible": push_possible,
        "gross_return_obligation_if_over": gross_return_if_over,
        "gross_return_obligation_if_under": gross_return_if_under,
        "gross_return_obligation_if_push": total_handle if push_possible else None,
        "pnl_swing": pnl_swing,
        "ledger_implied_need_side": need_side,
        "ledger_implied_need_tie": need_tie,
        "ledger_implied_need_magnitude": pnl_swing,
    }
