"""
simulation/offered_line.py

Constructs "ArbitrEdge's own offered line" for a game at a given
as_of, using ONLY real per-book point-in-time market observations
(features.market_state_as_of) -- never a model's directional output.

This reuses the exact algorithm already approved and shipped in
book_need/signal_line.py's select_representative_signal_line (an
unweighted lower-median across every book with a legitimate
observation at the cutoff -- no single-book dependency, no sportsbook
weighting), applied here directly to the RAW home_spread series
instead of to a side the Book Need engine has already inferred. That
is a deliberate difference from signal_line.get_available_signal_lines
(which takes an `inferred_side` parameter): depending on an
already-inferred side would pull a Book-Need-flavored concept into the
V3 decision path, so this module computes directly from raw per-book
spreads instead and never imports book_need.signal_line or anything
else from book_need.*.
"""

from features.market_state_as_of import market_state_as_of, PostKickoffCutoffError
from reporting.checkpoint_reconstruction import get_books_for_game
from simulation.accounting import implied_probability, InvalidPriceError

METHOD = "observed_lower_median_unweighted_available_books"
PRICE_METHOD = "representative_spread_books_median_implied_probability_actual_quote"
TOTAL_PRICE_METHOD = "representative_total_books_median_implied_probability_actual_quote"


def select_representative_price(matching, price_key):
    """
    Deterministic, row-order-independent representative price for one
    side, chosen ONLY from books tied for the representative spread
    (`matching`: a list of (book, state) pairs). Never fabricates a
    price (e.g. a default -110) -- the returned value, if any, is
    always one actually quoted by one of the matching books.

    American odds cannot be meaningfully median-ed as raw numbers
    across the +100/-100 boundary (e.g. the "numeric median" of -120
    and +110 is -5, which is not a legitimate price and does not sit
    between them in probability terms). Instead: convert every
    candidate price to its implied probability, take the LOWER median
    of those probabilities (same tie convention as the spread
    selection above, for consistency), and return the actual quoted
    price that produced it -- never a synthesized value.

    Sorting/tie-breaking is independent of `matching`'s input order:
    primary key is implied probability, secondary key is the book name
    (matching is only ever constructed from get_books_for_game's
    already-alphabetical output, but this function does not rely on
    that -- it re-sorts explicitly so correctness does not depend on
    callers keeping that invariant).

    Returns None if no matching book has a legitimate (non-None,
    non-zero) price for this side -- callers must then skip the wager
    rather than invent one.
    """
    candidates = []
    for book, state in matching:
        price = state.get(price_key)
        if price is None:
            continue
        try:
            prob = implied_probability(price)
        except InvalidPriceError:
            continue  # a 0 or otherwise illegitimate quote is not usable
        candidates.append((prob, book, price))

    if not candidates:
        return None

    candidates.sort(key=lambda row: (row[0], row[1]))
    count = len(candidates)
    midpoint = count // 2
    chosen = candidates[midpoint] if count % 2 == 1 else candidates[midpoint - 1]
    return chosen[2]


def construct_offered_line(conn, canonical_game_id, as_of):
    """
    Returns a dict:
        {
            "favorite_side": "home" | "away" | None,   # None only on a pick'em
            "spread_magnitude": float,
            "home_spread": float,                       # the representative value itself
            "home_price": float | None,
            "away_price": float | None,
            "book_count": int,
            "reference_books_used": [str, ...],
            "method": "observed_lower_median_unweighted_available_books",
            "price_method": "representative_spread_books_median_implied_probability_actual_quote",
        }
    or None if no book has a legitimate pregame observation at this
    as_of for this game. home_price/away_price are each independently
    None when no matching book quotes a legitimate price for that
    side -- population.py's wager selection is responsible for
    skipping (never fabricating a price for) a wager on a side with no
    legitimate price. See select_representative_price above.
    """
    books = get_books_for_game(conn, canonical_game_id)

    observations = []
    for book in books:
        try:
            state = market_state_as_of(conn, canonical_game_id, as_of, book)
        except PostKickoffCutoffError:
            continue
        if state is None or state.get("home_spread") is None:
            continue
        observations.append((book, state))

    if not observations:
        return None

    spreads = sorted(float(state["home_spread"]) for _, state in observations)
    count = len(spreads)
    midpoint = count // 2
    # Lower median on ties, matching signal_line.select_representative_signal_line.
    representative = spreads[midpoint] if count % 2 == 1 else spreads[midpoint - 1]

    matching = [
        (book, state) for book, state in observations
        if float(state["home_spread"]) == representative
    ]
    reference_books = sorted(book for book, _ in matching)

    home_price = select_representative_price(matching, "home_price")
    away_price = select_representative_price(matching, "away_price")

    if representative < 0:
        favorite_side = "home"
    elif representative > 0:
        favorite_side = "away"
    else:
        favorite_side = None  # pick'em -- no favorite to report

    return {
        "favorite_side": favorite_side,
        "spread_magnitude": abs(representative),
        "home_spread": representative,
        "home_price": home_price,
        "away_price": away_price,
        "book_count": count,
        "reference_books_used": reference_books,
        "method": METHOD,
        "price_method": PRICE_METHOD,
    }


def construct_offered_total(conn, canonical_game_id, as_of):
    """
    Construct ArbitrEdge\x27s representative point-in-time total using the
    same lower-median methodology as spreads. Over/under prices are selected
    only from books quoting the representative total and are always actual
    observed American-odds quotes; no fallback price is fabricated.
    """
    books = get_books_for_game(conn, canonical_game_id)

    observations = []
    for book in books:
        try:
            state = market_state_as_of(conn, canonical_game_id, as_of, book)
        except PostKickoffCutoffError:
            continue
        if state is None or state.get("total_points") is None:
            continue
        observations.append((book, state))

    if not observations:
        return None

    totals = sorted(float(state["total_points"]) for _, state in observations)
    count = len(totals)
    midpoint = count // 2
    representative = totals[midpoint] if count % 2 == 1 else totals[midpoint - 1]

    matching = [
        (book, state) for book, state in observations
        if float(state["total_points"]) == representative
    ]
    reference_books = sorted(book for book, _ in matching)

    over_price = select_representative_price(matching, "total_price_over")
    under_price = select_representative_price(matching, "total_price_under")

    return {
        "total_points": representative,
        "over_price": over_price,
        "under_price": under_price,
        "book_count": count,
        "reference_books_used": reference_books,
        "method": METHOD,
        "price_method": TOTAL_PRICE_METHOD,
    }
