"""
simulation/bet_selection.py

Game selection, side selection, stake and frequency selection for ONE
synthetic bettor, given:
  (1) a bettor profile (simulation/bettor_profile.py) -- persistent
      identity + personality traits,
  (2) raw, point-in-time game facts (engineered_features_v2-style raw
      columns only), and
  (3) ArbitrEdge's own offered line/price (simulation/offered_line.py).

ANTI-CIRCULARITY -- hard runtime guard, not just a convention
----------------------------------------------------------------
Every game-fact dict handed to this module is checked against
FORBIDDEN_GAME_FACT_KEYS before anything else happens. If a forbidden
key is present, CircularInputError is raised immediately. This module
does not trust its callers to keep Side Preference / Handle Intensity /
Public Pressure / split-consensus / market-response-regime / Book Need
fields out of the game dict -- it refuses to even look at a dict that
carries one.

This module imports nothing from book_need.public_pressure,
book_need.inference, book_need.synthetic_sportsbook, or behavioral.*.
See tests/test_anti_circularity_simulation.py for the AST-based static
proof of that.
"""

import math


class CircularInputError(ValueError):
    """Raised when a forbidden model-derived field reaches bet_selection."""


FORBIDDEN_GAME_FACT_KEYS = frozenset({
    "side_preference_direction",
    "side_preference_percentile",
    "side_preference_magnitude_percentile",
    "handle_intensity_percentile",
    "market_activity_percentile",
    "public_side",
    "aggregate_regime",
    "regime",
    "liability_score",
    "liability_strength",
    "book_need_side",
    "inferred_book_need_side",
    "need_strength",
    "decision",
})

# ---------------------------------------------------------------------
# EXPLICIT ALLOWLIST -- the only raw, point-in-time facts bet_selection
# is designed to use. This is deliberately an ALLOWLIST, not "every key
# except the forbidden ones": a denylist silently admits any future
# model-derived field that someone adds upstream before anyone remembers
# to add it to FORBIDDEN_GAME_FACT_KEYS. build_raw_game_facts() below is
# the one supported way to build a game-facts dict for this module from
# an arbitrary upstream dict -- it copies ONLY these keys, dropping
# everything else (known-forbidden or not-yet-named) unconditionally.
# FORBIDDEN_GAME_FACT_KEYS and _assert_no_forbidden_fields() remain as
# defense-in-depth for any caller that bypasses the allowlist and
# builds a game_facts dict by hand.
# ---------------------------------------------------------------------
ALLOWED_RAW_GAME_FACT_KEYS = frozenset({
    "home_wins_last_3", "away_wins_last_3",
    "home_wins_last_5", "away_wins_last_5",
    "home_points_for_avg_last_3", "away_points_for_avg_last_3",
    "home_points_against_avg_last_3", "away_points_against_avg_last_3",
    "home_point_margin_avg_last_3", "away_point_margin_avg_last_3",
    "home_point_margin_avg_last_5", "away_point_margin_avg_last_5",
    "home_current_win_streak", "away_current_win_streak",
    "home_days_since_last_game", "away_days_since_last_game",
    "home_last_game_point_margin", "away_last_game_point_margin",
    "competing_games_same_window_same_sport",
    "competing_games_same_window_all_sports",
    "is_standalone_same_sport", "is_standalone_all_sports",
    "day_of_week",
    "tv_network",
    "ranking_home", "ranking_away",
    "record_home", "record_away",
})


def build_raw_game_facts(upstream_game):
    """
    Build a sanitized game-facts dict for bet_selection from an
    arbitrary upstream dict (e.g. the legacy pool-game shape that also
    carries public_side/side_preference_percentile for the LEGACY
    engine). Copies ONLY keys in ALLOWED_RAW_GAME_FACT_KEYS -- anything
    else, named-forbidden or not, is dropped by construction rather
    than by exclusion.
    """
    return {k: upstream_game[k] for k in ALLOWED_RAW_GAME_FACT_KEYS if k in upstream_game}


def _assert_no_forbidden_fields(game_facts):
    present = FORBIDDEN_GAME_FACT_KEYS & set(game_facts.keys())
    if present:
        raise CircularInputError(
            f"bet_selection received forbidden model-derived field(s): {sorted(present)}"
        )


def _opposite(side):
    return "away" if side == "home" else "home"


def _recency_signal(game_facts, home_or_away):
    """
    Bounded [-1, 1] raw-recency signal for one side, built only from
    engineered_features_v2-style raw columns (wins/margin/streak) --
    never from any behavioral model's own output.
    """
    wins3 = game_facts.get(f"{home_or_away}_wins_last_3")
    margin3 = game_facts.get(f"{home_or_away}_point_margin_avg_last_3")
    streak = game_facts.get(f"{home_or_away}_current_win_streak")

    signal = 0.0
    components = 0

    if wins3 is not None:
        signal += (float(wins3) / 3.0) * 2.0 - 1.0
        components += 1
    if margin3 is not None:
        signal += max(-1.0, min(1.0, float(margin3) / 21.0))
        components += 1
    if streak is not None:
        signal += max(-1.0, min(1.0, float(streak) / 5.0))
        components += 1

    if components == 0:
        return 0.0
    return max(-1.0, min(1.0, signal / components))


def _rest_signal(game_facts, home_or_away):
    days = game_facts.get(f"{home_or_away}_days_since_last_game")
    if days is None:
        return 0.0
    return max(-1.0, min(1.0, (float(days) - 7.0) / 7.0))


def game_interest_weight(profile, game_facts):
    """
    Per-bettor probability weight for selecting THIS game among a
    slate. Mirrors the population-level weight already used in the
    legacy V2 engine (competing games, standalone, TV network,
    rankings presence) but modulated per-bettor by archetype traits
    instead of being population-uniform.
    """
    _assert_no_forbidden_fields(game_facts)

    weight = 1.0

    competing = game_facts.get("competing_games_same_window_same_sport")
    if competing is not None:
        weight *= 1.0 / math.sqrt(1.0 + 0.12 * max(0, int(competing)))

    if game_facts.get("is_standalone_same_sport") == 1:
        weight *= 1.0 + 0.45 * (0.5 + profile["standalone_primetime_bias"])

    network = str(game_facts.get("tv_network") or "").upper()
    if network and any(x in network for x in ("NBC", "ESPN", "ABC", "FOX", "CBS", "PRIME", "AMAZON")):
        weight *= 1.0 + 0.12 * (0.5 + profile["standalone_primetime_bias"])

    has_ranking = (
        game_facts.get("ranking_home") is not None
        or game_facts.get("ranking_away") is not None
    )
    if has_ranking:
        weight *= 1.0 + 0.30 * profile["ranked_team_attraction"]

    return max(0.05, weight)


def select_game(rng, profile, candidate_games):
    """
    candidate_games: list of (canonical_game_id, game_facts) tuples.
    Returns the selected canonical_game_id, or None if the list is empty.
    """
    if not candidate_games:
        return None

    weights = [game_interest_weight(profile, facts) for _, facts in candidate_games]
    total = sum(weights)
    target = rng.random() * total
    running = 0.0
    for (gid, _), w in zip(candidate_games, weights):
        running += w
        if target <= running:
            return gid
    return candidate_games[-1][0]


def select_spread_side(rng, profile, game_facts, offered_line):
    """
    offered_line: dict with at least {"favorite_side": "home"|"away",
    "spread_magnitude": float>=0}, as produced by
    simulation.offered_line.construct_offered_line -- ArbitrEdge's OWN
    offered line, built only from real per-book point-in-time market
    observations, never from any model's directional output.

    Returns (side, p_favorite) -- the selected side and the probability
    used, for auditability. Returns (None, None) if there is no usable
    offered line (caller should then skip this wager rather than
    inventing a side).
    """
    _assert_no_forbidden_fields(game_facts)

    favorite_side = offered_line.get("favorite_side") if offered_line else None
    if favorite_side not in ("home", "away"):
        return None, None

    underdog_side = _opposite(favorite_side)

    magnitude = max(0.0, float(offered_line.get("spread_magnitude") or 0.0))
    # Bounded [0,1] "how big is this spread" signal, saturating near 21
    # points (CFB spreads routinely exceed NFL's typical range).
    magnitude_signal = min(1.0, magnitude / 21.0)

    p = 0.50
    p += 0.25 * profile["favorite_bias"] * (1.0 - 0.4 * magnitude_signal)
    p -= 0.25 * profile["underdog_contrarian_bias"] * (1.0 - 0.4 * magnitude_signal)
    p += 0.10 * profile["home_team_bias"] * (1.0 if favorite_side == "home" else -1.0)

    # RELATIVE recency/rest: compare the favorite to the underdog, not
    # the favorite in isolation -- two equally hot (or equally rested)
    # teams must contribute ~nothing, never a favorite-side reward just
    # because both teams happen to be hot. Each _signal() call is
    # bounded [-1, 1], so each difference is bounded [-2, 2]; halving
    # rebounds it to [-1, 1].
    recency_fav = _recency_signal(game_facts, favorite_side)
    recency_dog = _recency_signal(game_facts, underdog_side)
    relative_recency = max(-1.0, min(1.0, (recency_fav - recency_dog) / 2.0))
    p += 0.15 * profile["recency_sensitivity"] * relative_recency

    rest_fav = _rest_signal(game_facts, favorite_side)
    rest_dog = _rest_signal(game_facts, underdog_side)
    relative_rest = max(-1.0, min(1.0, (rest_fav - rest_dog) / 2.0))
    p += 0.05 * profile["rest_sensitivity"] * relative_rest

    # Large underdog spreads can repel casual favorite-takers for CFB
    # bettors with low large_spread_tolerance. large_spread_tolerance is
    # CFB-only and is forced to exactly 0.0 for every NFL profile (see
    # bettor_profile.py) -- that 0.0 means "not applicable," NOT "zero
    # tolerance." Sport-gated so an inapplicable trait has ZERO effect
    # on NFL bettors rather than being silently reinterpreted as a real
    # behavioral value (a confirmed Phase 1 bug, fixed here).
    if profile.get("sport") == "cfb":
        tolerance = profile.get("large_spread_tolerance", 0.0)
        penalty = max(0.0, magnitude_signal - 0.5) * (1.0 - tolerance)
        p -= 0.20 * penalty

    p = max(0.05, min(0.95, p))

    side = favorite_side if rng.random() < p else _opposite(favorite_side)
    return side, p


def select_stake(rng, profile):
    """Same tier boundaries as the legacy V2 engine's _stake(), nudged
    per-bettor by stake_tier_shift."""
    shift = profile.get("stake_tier_shift", 0.0)
    u = rng.random()
    t1 = max(0.05, min(0.95, 0.72 + shift * 0.3))
    t2 = max(t1 + 0.01, min(0.99, 0.94 + shift * 0.2))
    t3 = max(t2 + 0.001, min(0.999, 0.992 + shift * 0.05))

    if u < t1:
        return rng.uniform(20, 125)
    if u < t2:
        return rng.uniform(125, 500)
    if u < t3:
        return rng.uniform(500, 2000)
    return rng.uniform(2000, 7500)


def select_wager_count(rng, profile):
    """Same tier boundaries as the legacy V2 engine's _wagers_for_bettor(),
    nudged per-bettor by bet_frequency_shift."""
    shift = profile.get("bet_frequency_shift", 0.0)
    u = rng.random()
    b1 = max(0.05, min(0.95, 0.28 - shift * 0.3))
    b2 = max(b1 + 0.01, min(0.97, 0.67 - shift * 0.2))
    b3 = max(b2 + 0.005, min(0.98, 0.87 - shift * 0.1))
    b4 = max(b3 + 0.002, min(0.99, 0.96 - shift * 0.05))
    b5 = max(b4 + 0.001, min(0.995, 0.99 - shift * 0.02))

    if u < b1:
        return 0
    if u < b2:
        return 1
    if u < b3:
        return 2
    if u < b4:
        return 3
    if u < b5:
        return 4
    return 5


def select_total_side(rng, profile, game_facts, offered_total):
    _assert_no_forbidden_fields(game_facts)

    total_points = offered_total.get("total_points")
    if total_points is None:
        return None, None

    scoring_keys = (
        "home_points_for_avg_last_3",
        "away_points_for_avg_last_3",
        "home_points_against_avg_last_3",
        "away_points_against_avg_last_3",
    )
    values = [game_facts.get(key) for key in scoring_keys]
    if any(value is None for value in values):
        p_over = 0.50
    else:
        home_pf, away_pf, home_pa, away_pa = map(float, values)
        recent_offense = home_pf + away_pf
        recent_defense = home_pa + away_pa
        expected_total = (recent_offense + recent_defense) / 2.0

        # A 14-point gap from the offered total is treated as a full
        # recent-scoring signal. Larger gaps are capped so a small sample
        # of extreme recent games cannot overwhelm bettor behavior.
        scoring_signal = max(
            -1.0,
            min(1.0, (expected_total - float(total_points)) / 14.0),
        )
        p_over = 0.50 + 0.25 * profile["recency_sensitivity"] * scoring_signal
        p_over = max(0.05, min(0.95, p_over))

    side = "over" if rng.random() < p_over else "under"
    return side, p_over
