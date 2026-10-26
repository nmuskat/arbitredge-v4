"""
tests/test_bet_selection.py

Phase 1. Standalone-runnable: python tests/test_bet_selection.py
"""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulation.bettor_profile import generate_profile
from simulation.bet_selection import (
    select_game, select_spread_side, select_total_side, select_stake, select_wager_count,
    game_interest_weight, CircularInputError, FORBIDDEN_GAME_FACT_KEYS,
    build_raw_game_facts, ALLOWED_RAW_GAME_FACT_KEYS,
)


CLEAN_GAME_FACTS = {
    "home_wins_last_3": 2, "away_wins_last_3": 1,
    "home_point_margin_avg_last_3": 4.0, "away_point_margin_avg_last_3": -2.0,
    "home_current_win_streak": 2, "away_current_win_streak": 0,
    "home_days_since_last_game": 7, "away_days_since_last_game": 7,
    "competing_games_same_window_same_sport": 3,
    "is_standalone_same_sport": 0,
    "tv_network": "CBS",
    "ranking_home": None, "ranking_away": None,
}


class TestForbiddenFieldGuard(unittest.TestCase):
    def test_game_interest_weight_rejects_forbidden_fields(self):
        profile = generate_profile("nfl_2026_v1", "nfl", 0)
        for key in FORBIDDEN_GAME_FACT_KEYS:
            dirty = dict(CLEAN_GAME_FACTS)
            dirty[key] = "anything"
            with self.assertRaises(CircularInputError):
                game_interest_weight(profile, dirty)

    def test_select_spread_side_rejects_forbidden_fields(self):
        profile = generate_profile("nfl_2026_v1", "nfl", 0)
        rng = random.Random(1)
        offered = {"favorite_side": "home", "spread_magnitude": 3.0}
        for key in FORBIDDEN_GAME_FACT_KEYS:
            dirty = dict(CLEAN_GAME_FACTS)
            dirty[key] = "anything"
            with self.assertRaises(CircularInputError):
                select_spread_side(rng, profile, dirty, offered)

    def test_clean_facts_pass_through(self):
        profile = generate_profile("nfl_2026_v1", "nfl", 0)
        # Should not raise.
        game_interest_weight(profile, CLEAN_GAME_FACTS)


class TestSelectGame(unittest.TestCase):
    def test_empty_candidates_returns_none(self):
        profile = generate_profile("nfl_2026_v1", "nfl", 0)
        rng = random.Random(1)
        self.assertIsNone(select_game(rng, profile, []))

    def test_single_candidate_always_selected(self):
        profile = generate_profile("nfl_2026_v1", "nfl", 0)
        rng = random.Random(1)
        gid = select_game(rng, profile, [(101, CLEAN_GAME_FACTS)])
        self.assertEqual(gid, 101)

    def test_determinism(self):
        profile = generate_profile("nfl_2026_v1", "nfl", 3)
        candidates = [(101, CLEAN_GAME_FACTS), (102, dict(CLEAN_GAME_FACTS, is_standalone_same_sport=1))]
        g1 = select_game(random.Random(99), profile, candidates)
        g2 = select_game(random.Random(99), profile, candidates)
        self.assertEqual(g1, g2)


class TestSelectSpreadSide(unittest.TestCase):
    def test_no_offered_line_returns_none_none(self):
        profile = generate_profile("nfl_2026_v1", "nfl", 0)
        rng = random.Random(1)
        side, p = select_spread_side(rng, profile, CLEAN_GAME_FACTS, {"favorite_side": None, "spread_magnitude": 0})
        self.assertIsNone(side)
        self.assertIsNone(p)

    def test_returns_home_or_away(self):
        profile = generate_profile("nfl_2026_v1", "nfl", 0)
        rng = random.Random(1)
        offered = {"favorite_side": "home", "spread_magnitude": 3.0}
        side, p = select_spread_side(rng, profile, CLEAN_GAME_FACTS, offered)
        self.assertIn(side, ("home", "away"))
        self.assertTrue(0.0 <= p <= 1.0)

    def test_favorite_bias_archetype_skews_toward_favorite(self):
        """
        CASUAL_FAVORITE-leaning profiles should pick the favorite side
        more often than CASUAL_BALANCED on average, across many draws.
        This is a behavioral sanity check, not an exact-value test.
        """
        offered = {"favorite_side": "home", "spread_magnitude": 3.0}

        def favorite_rate(bettor_index, trials=300):
            profile = generate_profile("nfl_2026_v1", "nfl", bettor_index)
            picks_favorite = 0
            for i in range(trials):
                rng = random.Random(1000 + i)
                side, _ = select_spread_side(rng, profile, CLEAN_GAME_FACTS, offered)
                if side == "home":
                    picks_favorite += 1
            return picks_favorite / trials

        # Find one CASUAL_FAVORITE and one UNDERDOG_CONTRARIAN bettor by
        # scanning indices (archetype assignment is deterministic).
        from simulation.bettor_profile import assign_archetype

        favorite_leaning_idx = None
        contrarian_idx = None
        for i in range(500):
            a, _version = assign_archetype("nfl", i)
            if a == "CASUAL_FAVORITE" and favorite_leaning_idx is None:
                favorite_leaning_idx = i
            if a == "UNDERDOG_CONTRARIAN" and contrarian_idx is None:
                contrarian_idx = i
            if favorite_leaning_idx is not None and contrarian_idx is not None:
                break

        self.assertIsNotNone(favorite_leaning_idx)
        self.assertIsNotNone(contrarian_idx)

        self.assertGreater(
            favorite_rate(favorite_leaning_idx), favorite_rate(contrarian_idx),
        )


class TestSelectStakeAndFrequency(unittest.TestCase):
    def test_stake_positive(self):
        profile = generate_profile("nfl_2026_v1", "nfl", 0)
        rng = random.Random(1)
        for _ in range(100):
            self.assertGreater(select_stake(rng, profile), 0)

    def test_wager_count_bounded(self):
        profile = generate_profile("nfl_2026_v1", "nfl", 0)
        rng = random.Random(1)
        for _ in range(200):
            n = select_wager_count(rng, profile)
            self.assertGreaterEqual(n, 0)
            self.assertLessEqual(n, 5)


class TestNflLargeSpreadToleranceIsNeutral(unittest.TestCase):
    """
    Regression test: large_spread_tolerance is CFB-only and forced to
    0.0 for every NFL profile. select_spread_side() previously
    reinterpreted that forced 0.0 as "zero tolerance," applying a real
    large-spread penalty to NFL bettors who should be unaffected by a
    trait that does not apply to them at all. Fixed by sport-gating the
    penalty entirely off profile["sport"] != "cfb".
    """

    def _nfl_profile(self, idx=0):
        p = generate_profile("nfl_2026_v1", "nfl", idx)
        self.assertEqual(p["sport"], "nfl")
        self.assertEqual(p["large_spread_tolerance"], 0.0)
        return p

    def test_large_spread_penalty_block_is_sport_gated_on_cfb(self):
        """
        Structural proof, not just a behavioral inference: the
        large-spread-tolerance penalty block in select_spread_side's
        source is guarded by `profile.get("sport") == "cfb"`, so
        changing its internals (coefficients, thresholds) can only ever
        affect CFB bettors -- an NFL profile's code path never reaches it.
        """
        import inspect
        source = inspect.getsource(select_spread_side)
        self.assertIn('profile.get("sport") == "cfb"', source)

    def test_varying_cfb_penalty_coefficient_cannot_move_nfl_probability(self):
        """
        Behavioral proof to accompany the structural one above: directly
        simulate "changing only the CFB large-spread logic" by calling
        select_spread_side with two different large_spread_tolerance
        values on the SAME nfl profile (large_spread_tolerance is
        forced 0.0 for real NFL profiles, but this proves the sport
        gate -- not merely the forced value -- is what keeps NFL safe,
        in case a future caller ever passed a non-zero value by mistake).
        """
        profile = self._nfl_profile()
        offered = {"favorite_side": "home", "spread_magnitude": 24.0}

        variant_a = dict(profile, large_spread_tolerance=0.0)
        variant_b = dict(profile, large_spread_tolerance=0.9)

        _s1, p_a = select_spread_side(random.Random(5), variant_a, CLEAN_GAME_FACTS, offered)
        _s2, p_b = select_spread_side(random.Random(5), variant_b, CLEAN_GAME_FACTS, offered)

        self.assertEqual(p_a, p_b, "an NFL profile's probability must not depend on large_spread_tolerance at all")

    def test_large_magnitude_does_not_move_nfl_probability_via_tolerance_term(self):
        """
        Directly: an NFL bettor facing a huge spread gets the exact same
        probability contribution regardless of magnitude once every
        other input is held fixed and only large_spread_tolerance's
        sport-gated term is in question -- i.e. varying spread magnitude
        changes other legitimate terms (home_team_bias sign, etc. are
        already fixed here) but the tolerance-penalty term itself must
        be exactly 0 at every magnitude for NFL.
        """
        profile = self._nfl_profile()
        small = {"favorite_side": "home", "spread_magnitude": 1.0}
        huge = {"favorite_side": "home", "spread_magnitude": 30.0}

        # Use the same rng draw for both so only the deterministic
        # probability differs, not the coin flip.
        _s1, p_small = select_spread_side(random.Random(7), profile, CLEAN_GAME_FACTS, small)
        _s2, p_huge = select_spread_side(random.Random(7), profile, CLEAN_GAME_FACTS, huge)

        # The only term that should vary with magnitude for NFL is the
        # favorite/underdog-bias dampening (* (1 - 0.4*magnitude_signal)),
        # NOT a large-spread-tolerance penalty (which is 0 for all
        # magnitudes since the CFB-only branch never executes). Confirm
        # by reproducing the expected p from the dampening term alone.
        favorite_bias = profile["favorite_bias"]
        underdog_bias = profile["underdog_contrarian_bias"]
        home_bias = profile["home_team_bias"]

        def expected_p(magnitude):
            magnitude_signal = min(1.0, magnitude / 21.0)
            p = 0.50
            p += 0.25 * favorite_bias * (1.0 - 0.4 * magnitude_signal)
            p -= 0.25 * underdog_bias * (1.0 - 0.4 * magnitude_signal)
            p += 0.10 * home_bias * 1.0  # favorite_side == "home"
            # recency/rest terms with CLEAN_GAME_FACTS computed separately below
            return p

        # Recompute full expected p including relative recency/rest so
        # this is an exact match, not just "in the right direction."
        from simulation.bet_selection import _recency_signal, _rest_signal
        recency_fav = _recency_signal(CLEAN_GAME_FACTS, "home")
        recency_dog = _recency_signal(CLEAN_GAME_FACTS, "away")
        rest_fav = _rest_signal(CLEAN_GAME_FACTS, "home")
        rest_dog = _rest_signal(CLEAN_GAME_FACTS, "away")
        relative_recency = max(-1.0, min(1.0, (recency_fav - recency_dog) / 2.0))
        relative_rest = max(-1.0, min(1.0, (rest_fav - rest_dog) / 2.0))

        def full_expected_p(magnitude):
            p = expected_p(magnitude)
            p += 0.15 * profile["recency_sensitivity"] * relative_recency
            p += 0.05 * profile["rest_sensitivity"] * relative_rest
            return max(0.05, min(0.95, p))

        self.assertAlmostEqual(p_small, full_expected_p(1.0), places=9)
        self.assertAlmostEqual(p_huge, full_expected_p(30.0), places=9)


class TestRelativeRecencyAndRest(unittest.TestCase):
    """
    select_spread_side must compare the favorite's recent form/rest to
    the UNDERDOG's, not evaluate the favorite in isolation -- two
    equally hot (or equally rested) teams must contribute ~zero signal.
    """

    def _profile_with_sensitivity(self):
        # Find a profile with meaningfully non-zero recency_sensitivity
        # and rest_sensitivity so the effect is measurable.
        for i in range(200):
            p = generate_profile("nfl_2026_v1", "nfl", i)
            if p["recency_sensitivity"] > 0.3 and p["rest_sensitivity"] > 0.3:
                return p
        self.fail("no profile found with sufficient sensitivity for this test")

    def test_equal_recent_form_is_neutral(self):
        profile = self._profile_with_sensitivity()
        equal_form_facts = dict(CLEAN_GAME_FACTS)
        equal_form_facts["home_wins_last_3"] = 2
        equal_form_facts["away_wins_last_3"] = 2
        equal_form_facts["home_point_margin_avg_last_3"] = 5.0
        equal_form_facts["away_point_margin_avg_last_3"] = 5.0
        equal_form_facts["home_current_win_streak"] = 2
        equal_form_facts["away_current_win_streak"] = 2
        equal_form_facts["home_days_since_last_game"] = 7
        equal_form_facts["away_days_since_last_game"] = 7

        offered = {"favorite_side": "home", "spread_magnitude": 3.0}
        _side, p = select_spread_side(random.Random(1), profile, equal_form_facts, offered)

        baseline = 0.50
        baseline += 0.25 * profile["favorite_bias"] * (1.0 - 0.4 * (3.0 / 21.0))
        baseline -= 0.25 * profile["underdog_contrarian_bias"] * (1.0 - 0.4 * (3.0 / 21.0))
        baseline += 0.10 * profile["home_team_bias"]
        baseline = max(0.05, min(0.95, baseline))

        self.assertAlmostEqual(p, baseline, places=6)

    def test_favorite_hotter_than_dog_increases_favorite_probability(self):
        profile = self._profile_with_sensitivity()
        facts = dict(CLEAN_GAME_FACTS)
        facts.update({
            "home_wins_last_3": 3, "home_point_margin_avg_last_3": 14.0, "home_current_win_streak": 4,
            "away_wins_last_3": 0, "away_point_margin_avg_last_3": -14.0, "away_current_win_streak": 0,
            "home_days_since_last_game": 7, "away_days_since_last_game": 7,
        })
        offered = {"favorite_side": "home", "spread_magnitude": 3.0}
        _side, p_hot_favorite = select_spread_side(random.Random(1), profile, facts, offered)

        equal_facts = dict(facts)
        equal_facts["away_wins_last_3"] = 3
        equal_facts["away_point_margin_avg_last_3"] = 14.0
        equal_facts["away_current_win_streak"] = 4
        _side2, p_equal = select_spread_side(random.Random(1), profile, equal_facts, offered)

        self.assertGreater(p_hot_favorite, p_equal)

    def test_dog_hotter_than_favorite_decreases_favorite_probability(self):
        profile = self._profile_with_sensitivity()
        facts = dict(CLEAN_GAME_FACTS)
        facts.update({
            "home_wins_last_3": 0, "home_point_margin_avg_last_3": -14.0, "home_current_win_streak": 0,
            "away_wins_last_3": 3, "away_point_margin_avg_last_3": 14.0, "away_current_win_streak": 4,
            "home_days_since_last_game": 7, "away_days_since_last_game": 7,
        })
        offered = {"favorite_side": "home", "spread_magnitude": 3.0}
        _side, p_cold_favorite = select_spread_side(random.Random(1), profile, facts, offered)

        equal_facts = dict(facts)
        equal_facts["home_wins_last_3"] = 3
        equal_facts["home_point_margin_avg_last_3"] = 14.0
        equal_facts["home_current_win_streak"] = 4
        _side2, p_equal = select_spread_side(random.Random(1), profile, equal_facts, offered)

        self.assertLess(p_cold_favorite, p_equal)

    def test_equal_rest_is_neutral_holding_recency_equal_too(self):
        profile = self._profile_with_sensitivity()
        facts = dict(CLEAN_GAME_FACTS)
        facts.update({
            "home_wins_last_3": 2, "away_wins_last_3": 2,
            "home_point_margin_avg_last_3": 5.0, "away_point_margin_avg_last_3": 5.0,
            "home_current_win_streak": 2, "away_current_win_streak": 2,
            "home_days_since_last_game": 10, "away_days_since_last_game": 10,
        })
        offered = {"favorite_side": "home", "spread_magnitude": 3.0}
        _side, p = select_spread_side(random.Random(1), profile, facts, offered)

        baseline = 0.50
        baseline += 0.25 * profile["favorite_bias"] * (1.0 - 0.4 * (3.0 / 21.0))
        baseline -= 0.25 * profile["underdog_contrarian_bias"] * (1.0 - 0.4 * (3.0 / 21.0))
        baseline += 0.10 * profile["home_team_bias"]
        baseline = max(0.05, min(0.95, baseline))

        self.assertAlmostEqual(p, baseline, places=6)


class TestSelectTotalSide(unittest.TestCase):
    def test_no_offered_total_returns_none_none(self):
        profile = generate_profile("nfl_2026_v1", "nfl", 0)
        side, p = select_total_side(random.Random(1), profile, CLEAN_GAME_FACTS, {"total_points": None})
        self.assertIsNone(side)
        self.assertIsNone(p)

    def test_higher_recent_scoring_increases_over_probability(self):
        profile = dict(generate_profile("nfl_2026_v1", "nfl", 0), recency_sensitivity=1.0)
        offered = {"total_points": 47.5}

        high = dict(CLEAN_GAME_FACTS)
        high.update({
            "home_points_for_avg_last_3": 35.0,
            "away_points_for_avg_last_3": 31.0,
            "home_points_against_avg_last_3": 28.0,
            "away_points_against_avg_last_3": 30.0,
        })

        low = dict(CLEAN_GAME_FACTS)
        low.update({
            "home_points_for_avg_last_3": 17.0,
            "away_points_for_avg_last_3": 16.0,
            "home_points_against_avg_last_3": 18.0,
            "away_points_against_avg_last_3": 17.0,
        })

        _side_high, p_high = select_total_side(random.Random(1), profile, high, offered)
        _side_low, p_low = select_total_side(random.Random(1), profile, low, offered)
        self.assertGreater(p_high, p_low)

    def test_rejects_forbidden_fields(self):
        profile = generate_profile("nfl_2026_v1", "nfl", 0)
        offered = {"total_points": 47.5}
        for key in FORBIDDEN_GAME_FACT_KEYS:
            dirty = dict(CLEAN_GAME_FACTS)
            dirty[key] = "anything"
            with self.assertRaises(CircularInputError):
                select_total_side(random.Random(1), profile, dirty, offered)

    def test_returns_over_or_under(self):
        profile = generate_profile("nfl_2026_v1", "nfl", 0)
        facts = dict(CLEAN_GAME_FACTS)
        facts.update({
            "home_points_for_avg_last_3": 27.0,
            "away_points_for_avg_last_3": 24.0,
            "home_points_against_avg_last_3": 21.0,
            "away_points_against_avg_last_3": 23.0,
        })
        side, p = select_total_side(random.Random(1), profile, facts, {"total_points": 47.5})
        self.assertIn(side, ("over", "under"))
        self.assertTrue(0.0 <= p <= 1.0)


class TestAllowlistAdapter(unittest.TestCase):
    """
    build_raw_game_facts() must be an ALLOWLIST: an unknown/unapproved
    upstream field (not in FORBIDDEN_GAME_FACT_KEYS either -- i.e. a
    hypothetical future model-derived field nobody has named yet) must
    not cross the boundary just because nobody denylisted it.
    """

    def test_unknown_field_does_not_cross_the_boundary(self):
        upstream = dict(CLEAN_GAME_FACTS)
        upstream["some_future_model_output_nobody_named_yet"] = 99.9
        upstream["another_unapproved_field"] = "x"

        sanitized = build_raw_game_facts(upstream)

        self.assertNotIn("some_future_model_output_nobody_named_yet", sanitized)
        self.assertNotIn("another_unapproved_field", sanitized)

    def test_only_allowed_keys_survive(self):
        upstream = dict(CLEAN_GAME_FACTS)
        upstream["public_side"] = "home"  # a known-forbidden field too
        sanitized = build_raw_game_facts(upstream)
        self.assertTrue(set(sanitized.keys()).issubset(ALLOWED_RAW_GAME_FACT_KEYS))
        self.assertNotIn("public_side", sanitized)

    def test_allowed_values_are_preserved(self):
        sanitized = build_raw_game_facts(CLEAN_GAME_FACTS)
        for k in CLEAN_GAME_FACTS:
            if k in ALLOWED_RAW_GAME_FACT_KEYS:
                self.assertEqual(sanitized[k], CLEAN_GAME_FACTS[k])

    def test_sanitized_output_passes_the_runtime_guard_too(self):
        upstream = dict(CLEAN_GAME_FACTS, public_side="home", side_preference_percentile=70)
        sanitized = build_raw_game_facts(upstream)
        profile = generate_profile("nfl_2026_v1", "nfl", 0)
        # Should not raise -- defense-in-depth guard and allowlist agree.
        game_interest_weight(profile, sanitized)


if __name__ == "__main__":
    unittest.main()
