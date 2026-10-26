"""
behavioral/handle_intensity_cfb_v2.py

CFB Handle Intensity V2
-------------------
Experimental, uncalibrated proxy for relative betting-market pressure.

Purpose:
    Estimate how unusually active/broad the observable betting market is
    around a game relative to the comparison population.

This is NOT actual sportsbook handle and does not claim to observe private
sportsbook liability.

Design principles:
    - Direction-neutral. Side Preference handles direction.
    - Strictly point-in-time: never reads snapshots after each game's as_of.
    - Market-behavior based rather than matchup/popularity assumptions.
    - Equal-weighted standardized components; no fitted parameters.
    - Raw snapshot count is NOT used as a feature.
    - Existing Handle Intensity V1 remains untouched.

Primary components:
    1. Spread change events per active book over prior 6 hours.
    2. Spread-price (juice) change events per active book over prior 6 hours.
    3. Share of active books that changed spread or juice over prior 6 hours.
    4. Activity acceleration: final-hour event rate versus preceding five hours.
    5. Coordinated activity: 10-minute buckets in which >=2 books changed.

Percentile is relative to the supplied comparison population.
"""

from datetime import timedelta
from math import sqrt


VERSION = "handle_intensity_cfb_v2"
WINDOW_HOURS = 6
LATE_WINDOW_HOURS = 1
COORDINATION_BUCKET_MINUTES = 10


def _parse_iso(value):
    from datetime import datetime, timezone

    if value is None:
        return None

    value = str(value).strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def _mean(values):
    if not values:
        return None
    return sum(values) / len(values)


def _population_std(values):
    if not values:
        return None

    mean = _mean(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return sqrt(variance)


def _zscore(value, reference_values):
    if value is None or not reference_values:
        return 0.0

    mean = _mean(reference_values)
    std = _population_std(reference_values)

    if std is None or std == 0:
        return 0.0

    return (value - mean) / std


def _percentile_rank(value, values):
    """
    Mid-rank percentile.

    Equal values receive the same percentile. This avoids arbitrary ordering
    among tied games.
    """
    if value is None or not values:
        return None

    below = sum(1 for x in values if x < value)
    equal = sum(1 for x in values if x == value)

    return 100.0 * (below + 0.5 * equal) / len(values)


def _bucket_start(dt, minutes=COORDINATION_BUCKET_MINUTES):
    minute = (dt.minute // minutes) * minutes
    return dt.replace(minute=minute, second=0, microsecond=0)


def _market_activity_for_game(conn, canonical_game_id, as_of):
    """
    Compute direction-neutral market activity using only observations known
    by as_of.

    We fetch one extra hour before the six-hour scoring window so a book's
    first observation inside the scoring window can be compared with a
    legitimate earlier state. Events themselves are counted only when the
    newer observation falls inside the scoring window.
    """
    as_of_dt = _parse_iso(as_of)

    if as_of_dt is None:
        return None

    window_start = as_of_dt - timedelta(hours=WINDOW_HOURS)
    baseline_start = window_start - timedelta(hours=1)
    late_start = as_of_dt - timedelta(hours=LATE_WINDOW_HOURS)

    rows = conn.execute(
        """
        SELECT
            book,
            observed_at,
            spread_home,
            spread_price_home
        FROM market_snapshots
        WHERE canonical_game_id = ?
          AND observed_at >= ?
          AND observed_at <= ?
        ORDER BY book, observed_at
        """,
        (
            canonical_game_id,
            baseline_start.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            as_of_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        ),
    ).fetchall()

    by_book = {}

    for book, observed_at, spread_home, spread_price_home in rows:
        by_book.setdefault(book, []).append(
            (
                _parse_iso(observed_at),
                spread_home,
                spread_price_home,
            )
        )

    active_books = 0
    moving_books = set()

    spread_changes = 0
    juice_changes = 0

    late_events = 0
    prior_five_hour_events = 0

    coordinated_buckets = {}

    for book, observations in by_book.items():
        scoring_observations = [
            row for row in observations
            if row[0] >= window_start
        ]

        if scoring_observations:
            active_books += 1

        previous = None

        for observed_dt, spread, price in observations:
            if previous is None:
                previous = (spread, price)
                continue

            prev_spread, prev_price = previous

            spread_changed = (
                observed_dt >= window_start
                and spread is not None
                and prev_spread is not None
                and spread != prev_spread
            )

            juice_changed = (
                observed_dt >= window_start
                and price is not None
                and prev_price is not None
                and price != prev_price
            )

            if spread_changed:
                spread_changes += 1

            if juice_changed:
                juice_changes += 1

            if spread_changed or juice_changed:
                moving_books.add(book)

                event_count = int(spread_changed) + int(juice_changed)

                if observed_dt >= late_start:
                    late_events += event_count
                else:
                    prior_five_hour_events += event_count

                bucket = _bucket_start(observed_dt)
                coordinated_buckets.setdefault(bucket, set()).add(book)

            previous = (spread, price)

    if active_books == 0:
        return {
            "spread_change_rate": None,
            "juice_change_rate": None,
            "moving_book_share": None,
            "activity_acceleration": None,
            "coordinated_activity": None,
            "active_books": 0,
            "moving_books": 0,
            "spread_changes": 0,
            "juice_changes": 0,
            "late_events": 0,
            "prior_five_hour_events": 0,
            "coordinated_buckets": 0,
        }

    spread_change_rate = spread_changes / active_books
    juice_change_rate = juice_changes / active_books
    moving_book_share = len(moving_books) / active_books

    # Compare event rates, not raw counts:
    # final 1 hour versus the hourly rate of the preceding 5 hours.
    late_hourly_rate = late_events
    prior_hourly_rate = prior_five_hour_events / 5.0

    # Difference rather than ratio avoids unstable explosions when the
    # preceding five-hour rate is zero.
    activity_acceleration = late_hourly_rate - prior_hourly_rate

    coordinated_count = sum(
        1
        for books in coordinated_buckets.values()
        if len(books) >= 2
    )

    # Normalize coordinated activity by active books so a game with fewer
    # observable books is not automatically disadvantaged.
    coordinated_activity = coordinated_count / active_books

    return {
        "spread_change_rate": spread_change_rate,
        "juice_change_rate": juice_change_rate,
        "moving_book_share": moving_book_share,
        "activity_acceleration": activity_acceleration,
        "coordinated_activity": coordinated_activity,
        "active_books": active_books,
        "moving_books": len(moving_books),
        "spread_changes": spread_changes,
        "juice_changes": juice_changes,
        "late_events": late_events,
        "prior_five_hour_events": prior_five_hour_events,
        "coordinated_buckets": coordinated_count,
    }


PRIMARY_COMPONENTS = (
    "spread_change_rate",
    "juice_change_rate",
    "moving_book_share",
    "activity_acceleration",
    "coordinated_activity",
)


def compute_handle_intensity_v2(conn, games):
    """
    games must contain:
        canonical_game_id
        _hi_as_of

    Returns one result per game.

    All primary components receive equal weight after cross-sectional
    standardization. Missing components are excluded from that game's
    normalized score rather than imputed as evidence.
    """
    raw_by_game = {}

    for game in games:
        gid = game["canonical_game_id"]
        as_of = game.get("_hi_as_of")

        if as_of is None:
            raw_by_game[gid] = None
            continue

        # Never allow a comparison game's HI window to consume observations
        # at or after that game's kickoff.
        kickoff_row = conn.execute(
            "SELECT commence_time FROM games_canonical "
            "WHERE canonical_game_id=?",
            (gid,),
        ).fetchone()

        if kickoff_row is not None and kickoff_row[0] is not None:
            as_of_dt = _parse_iso(as_of)
            kickoff_dt = _parse_iso(kickoff_row[0])

            if as_of_dt >= kickoff_dt:
                as_of = (
                    kickoff_dt - timedelta(microseconds=1)
                ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        raw_by_game[gid] = _market_activity_for_game(
            conn,
            gid,
            as_of,
        )

    reference = {}

    for component in PRIMARY_COMPONENTS:
        reference[component] = [
            raw[component]
            for raw in raw_by_game.values()
            if raw is not None and raw.get(component) is not None
        ]

    results = []

    for game in games:
        gid = game["canonical_game_id"]
        raw = raw_by_game.get(gid)

        if raw is None:
            results.append({
                "canonical_game_id": gid,
                "version": VERSION,
                "normalized_score": None,
                "percentile_rank": None,
                "coverage_count": 0,
                "components": {},
                "raw": None,
            })
            continue

        component_scores = {}
        usable_scores = []

        for component in PRIMARY_COMPONENTS:
            value = raw.get(component)

            if value is None:
                continue

            score = _zscore(
                value,
                reference[component],
            )

            component_scores[component] = score
            usable_scores.append(score)

        normalized_score = (
            sum(usable_scores) / len(usable_scores)
            if usable_scores
            else None
        )

        results.append({
            "canonical_game_id": gid,
            "version": VERSION,
            "normalized_score": normalized_score,
            "percentile_rank": None,
            "coverage_count": len(usable_scores),
            "components": component_scores,
            "raw": raw,
        })

    valid_scores = [
        result["normalized_score"]
        for result in results
        if result["normalized_score"] is not None
    ]

    for result in results:
        if result["normalized_score"] is not None:
            result["percentile_rank"] = _percentile_rank(
                result["normalized_score"],
                valid_scores,
            )

    return results

# ---------------------------------------------------------------------------
# CFB Market Importance — supplemental context layer
# ---------------------------------------------------------------------------

def _market_importance_for_game(conn, canonical_game_id, as_of):
    """
    Return positive pregame context evidence about the relative importance
    of a CFB market.

    Missing context is treated as UNKNOWN, never as evidence that a game is
    unimportant. This function is strictly point-in-time and does not use
    game results, post-kickoff context, or raw snapshot volume.

    This layer is diagnostic only until separately validated.
    """
    as_of_dt = _parse_iso(as_of)
    if as_of_dt is None:
        return None

    row = conn.execute(
        """
        SELECT
            ranking_home,
            ranking_away,
            tv_network,
            kickoff_window,
            observed_at
        FROM game_context
        WHERE canonical_game_id = ?
          AND observed_at <= ?
        ORDER BY observed_at DESC
        LIMIT 1
        """,
        (
            canonical_game_id,
            as_of_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        ),
    ).fetchone()

    if row is None:
        return {
            "context_available": False,
            "ranked_team_count": None,
            "best_rank": None,
            "both_ranked": None,
            "tv_network": None,
            "has_tv_network": None,
            "kickoff_window": None,
        }

    ranking_home, ranking_away, tv_network, kickoff_window, _ = row

    rankings = [
        int(rank)
        for rank in (ranking_home, ranking_away)
        if rank is not None
    ]

    return {
        "context_available": True,
        "ranked_team_count": len(rankings),
        "best_rank": min(rankings) if rankings else None,
        "both_ranked": 1 if len(rankings) == 2 else 0,
        "tv_network": tv_network,
        "has_tv_network": 1 if tv_network else 0,
        "kickoff_window": kickoff_window,
    }

def _market_importance_score(info):
    """
    Conservative diagnostic score for positive CFB market-importance evidence.

    Numerical importance currently uses ranking evidence only:
      - ranked_team_count: two ranked teams > one ranked team;
      - rank_strength: higher-ranked teams provide stronger evidence.

    TV network is retained as descriptive metadata but is NOT numerically
    scored because simple broadcast presence does not distinguish a marquee
    national market from ordinary cable/streaming availability.

    Missing context returns None rather than zero so missing ESPN coverage is
    never interpreted as evidence of low market importance.
    """
    if not info or not info.get("context_available"):
        return None

    ranked_count = info.get("ranked_team_count")
    best_rank = info.get("best_rank")

    if ranked_count is None or ranked_count == 0 or best_rank is None:
        return None

    ranked_component = ranked_count / 2.0

    rank_strength = (
        (26.0 - best_rank) / 25.0
        if 1 <= best_rank <= 25
        else None
    )

    components = [
        x for x in (
            ranked_component,
            rank_strength,
        )
        if x is not None
    ]

    if not components:
        return None

    return {
        "score": sum(components) / len(components),
        "ranked_component": ranked_component,
        "rank_strength": rank_strength,
        "tv_component": None,
        "component_count": len(components),
    }

def classify_cfb_market(activity_percentile, importance):
    """
    Experimental CFB market-relevance classification.

    This separates observable market activity from independently available
    ranking-based importance evidence rather than blending them into a single
    opaque score.

    Diagnostic only. These classifications do not currently gate or create
    ArbitrEdge directional signals.
    """
    activity = (
        float(activity_percentile)
        if activity_percentile is not None
        else None
    )

    importance_score = (
        float(importance["score"])
        if importance and importance.get("score") is not None
        else None
    )

    # Strong convergence: clearly elevated activity plus strong independent
    # importance evidence.
    if (
        activity is not None
        and activity >= 60.0
        and importance_score is not None
        and importance_score >= 0.60
    ):
        classification = "CORE"

    # Exceptional observable activity can stand on its own when contextual
    # importance evidence is missing rather than falsely treating missing
    # context as low importance.
    elif activity is not None and activity >= 90.0:
        classification = "ACTIVITY"

    # Strong independent importance evidence identifies games worth watching,
    # but does not itself imply sufficient betting-market pressure.
    elif importance_score is not None and importance_score >= 0.75:
        classification = "IMPORTANCE"

    else:
        classification = "STANDARD"

    return {
        "classification": classification,
        "activity_percentile": activity,
        "importance_score": importance_score,
    }

def classify_mixed_response_composition(segment):
    """
    Diagnostic-only decomposition of a MIXED market-response segment.

    Book-side responses:
      RESISTANCE, DIVERGENCE, PARTIAL_DIVERGENCE

    Public-side responses:
      CONFIRMATION

    Neutral responses:
      NO_MOVEMENT

    This helper does not alter production inference or create signals.
    """
    if not segment or segment.get("aggregate_regime") != "MIXED":
        return None

    regimes = [
        row.get("regime")
        for row in segment.get("per_book", {}).values()
        if row.get("regime") != "INSUFFICIENT"
    ]

    book_side = sum(
        regime in ("RESISTANCE", "DIVERGENCE", "PARTIAL_DIVERGENCE")
        for regime in regimes
    )
    confirmation = sum(regime == "CONFIRMATION" for regime in regimes)
    neutral = sum(regime == "NO_MOVEMENT" for regime in regimes)
    usable = len(regimes)

    if book_side > confirmation:
        composition = "BOOK_SIDE_DOMINANT"
    elif confirmation > book_side:
        composition = "CONFIRMATION_DOMINANT"
    else:
        composition = "TIED"

    return {
        "composition": composition,
        "book_side_count": book_side,
        "confirmation_count": confirmation,
        "neutral_count": neutral,
        "usable_book_count": usable,
        "book_side_share": (book_side / usable) if usable else None,
        "confirmation_share": (confirmation / usable) if usable else None,
    }
