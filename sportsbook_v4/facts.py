"""
sportsbook_v4/facts.py -- point-in-time raw game facts for bettor decisions.

ANTI-CIRCULARITY: only keys in ALLOWED_FACT_KEYS ever reach a bettor.
The list mirrors simulation/bet_selection.py's ALLOWED_RAW_GAME_FACT_KEYS
(raw schedule/recency/context columns). Any model-derived field (Side
Preference, Handle Intensity, Public Pressure, regimes, Book Need,
liability, decisions) is in FORBIDDEN_FACT_KEYS and is rejected loudly if
a fact source ever tries to supply it.
"""

import bisect

ALLOWED_FACT_KEYS = frozenset({
    "home_wins_last_3", "away_wins_last_3",
    "home_point_margin_avg_last_3", "away_point_margin_avg_last_3",
    "home_current_win_streak", "away_current_win_streak",
    "home_days_since_last_game", "away_days_since_last_game",
    "competing_games_same_window_same_sport",
    "is_standalone_same_sport", "tv_network",
    "ranking_home", "ranking_away",
})

FORBIDDEN_FACT_KEYS = frozenset({
    "side_preference_direction", "side_preference_percentile",
    "side_preference_magnitude_percentile", "handle_intensity_percentile",
    "market_activity_percentile", "public_side", "aggregate_regime", "regime",
    "liability_score", "liability_strength", "book_need_side",
    "inferred_book_need_side", "need_strength", "decision",
    "financial_need_side", "official_play",
})


class CircularInputError(ValueError):
    pass


def sanitize(facts):
    bad = FORBIDDEN_FACT_KEYS & set(facts)
    if bad:
        raise CircularInputError(f"forbidden model-derived fact(s): {sorted(bad)}")
    return {k: facts[k] for k in ALLOWED_FACT_KEYS if k in facts}


class FactsSource:
    def facts(self, game_id, as_of_epoch):
        raise NotImplementedError


class NoFacts(FactsSource):
    def facts(self, game_id, as_of_epoch):
        return {}


class FixtureFacts(FactsSource):
    """{game_id: [(effective_epoch, facts_dict), ...]} -> latest at or before as_of."""

    def __init__(self, timeline):
        self._t = {}
        for gid, rows in timeline.items():
            rows = sorted(rows, key=lambda r: r[0])
            self._t[gid] = ([r[0] for r in rows], [sanitize(r[1]) for r in rows])

    def facts(self, game_id, as_of_epoch):
        if game_id not in self._t:
            return {}
        epochs, vals = self._t[game_id]
        i = bisect.bisect_right(epochs, as_of_epoch)
        return dict(vals[i - 1]) if i else {}


def recency_signal(facts, side):
    """Bounded [-1, 1] raw-recency signal for one side (wins/margin/streak)."""
    parts = []
    w = facts.get(f"{side}_wins_last_3")
    if w is not None:
        parts.append((float(w) / 3.0) * 2.0 - 1.0)
    m = facts.get(f"{side}_point_margin_avg_last_3")
    if m is not None:
        parts.append(max(-1.0, min(1.0, float(m) / 21.0)))
    s = facts.get(f"{side}_current_win_streak")
    if s is not None:
        parts.append(max(-1.0, min(1.0, float(s) / 5.0)))
    return sum(parts) / len(parts) if parts else 0.0
