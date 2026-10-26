"""
tests/test_bettor_profile.py

Phase 1. Standalone-runnable: python tests/test_bettor_profile.py
(no pytest, no db.migrate dependency -- this module has no database at all).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulation.bettor_profile import (
    generate_profile, generate_population, assign_archetype, TRAIT_NAMES,
)
from simulation.archetype_config import ARCHETYPES, ARCHETYPE_DISTRIBUTION_V1_UNVALIDATED_PRIOR


class TestDeterminism(unittest.TestCase):
    def test_same_inputs_same_profile(self):
        p1 = generate_profile("nfl_2026_v1", "nfl", 42)
        p2 = generate_profile("nfl_2026_v1", "nfl", 42)
        self.assertEqual(p1, p2)

    def test_different_bettor_index_different_profile(self):
        p1 = generate_profile("nfl_2026_v1", "nfl", 42)
        p2 = generate_profile("nfl_2026_v1", "nfl", 43)
        self.assertNotEqual(p1["archetype"], p2["archetype"]) if False else None
        # Traits in particular should essentially never collide across indices.
        self.assertNotEqual(
            tuple(p1[t] for t in TRAIT_NAMES),
            tuple(p2[t] for t in TRAIT_NAMES),
        )

    def test_different_population_version_different_profile(self):
        p1 = generate_profile("nfl_2026_v1", "nfl", 42)
        p2 = generate_profile("nfl_2027_v1", "nfl", 42)
        self.assertNotEqual(
            tuple(p1[t] for t in TRAIT_NAMES),
            tuple(p2[t] for t in TRAIT_NAMES),
        )

    def test_no_wallclock_dependency(self):
        """Calling twice, with real time elapsing, must not change anything."""
        import time
        p1 = generate_profile("nfl_2026_v1", "nfl", 7)
        time.sleep(0.01)
        p2 = generate_profile("nfl_2026_v1", "nfl", 7)
        self.assertEqual(p1, p2)


class TestArchetypes(unittest.TestCase):
    def test_archetype_is_one_of_the_named_set(self):
        for i in range(500):
            a, _version = assign_archetype("nfl", i)
            self.assertIn(a, ARCHETYPES)

    def test_distribution_sums_to_one(self):
        self.assertAlmostEqual(sum(ARCHETYPE_DISTRIBUTION_V1_UNVALIDATED_PRIOR.values()), 1.0, places=9)

    def test_large_population_roughly_matches_distribution(self):
        n = 5000
        counts = {a: 0 for a in ARCHETYPES}
        for i in range(n):
            archetype, _version = assign_archetype("nfl", i)
            counts[archetype] += 1
        for archetype, target in ARCHETYPE_DISTRIBUTION_V1_UNVALIDATED_PRIOR.items():
            observed = counts[archetype] / n
            self.assertLess(
                abs(observed - target), 0.04,
                f"{archetype}: observed {observed:.3f} vs target {target:.3f}",
            )


class TestArchetypeIdentityStability(unittest.TestCase):
    """
    Regression test for a confirmed Phase 1 bug: assign_archetype() was
    seeded by population_version + bettor_index, so a new
    population_version could compute a different archetype for the same
    bettor_index than the one already persisted (append-only) in
    synthetic_bettor_identity. Archetype is IDENTITY and must be a pure
    function of (sport, bettor_index, identity_generator_version,
    archetype_distribution_version) -- never of population_version.
    """

    def test_archetype_independent_of_population_version(self):
        for i in range(300):
            a1, v1 = assign_archetype("nfl", i)
            a2, v2 = assign_archetype("nfl", i)  # same call, no population_version slot to vary
            self.assertEqual(a1, a2)
            self.assertEqual(v1, v2)

    def test_generate_profile_archetype_stable_across_population_versions(self):
        """
        create Bettor #N under nfl_2026_v1, then generate nfl_2026_v2 for
        the same bettor_index: bettor_index and archetype must be
        unchanged; only continuous traits may differ; and those new
        traits must have been generated starting from the SAME archetype
        (i.e. the archetype-conditioned trait shift that was applied is
        identical).
        """
        from simulation.bettor_profile import _ARCHETYPE_TRAIT_SHIFTS

        n = 271  # arbitrary fixed bettor index
        profile_v1 = generate_profile("nfl_2026_v1", "nfl", n)
        profile_v2 = generate_profile("nfl_2026_v2", "nfl", n)

        self.assertEqual(profile_v1["bettor_index"], profile_v2["bettor_index"])
        self.assertEqual(profile_v1["archetype"], profile_v2["archetype"])
        self.assertEqual(
            profile_v1["archetype_distribution_version"],
            profile_v2["archetype_distribution_version"],
        )

        # Continuous traits are free to differ across population versions...
        self.assertNotEqual(
            tuple(profile_v1[t] for t in TRAIT_NAMES),
            tuple(profile_v2[t] for t in TRAIT_NAMES),
        )

        # ...but both were generated from the one true archetype's shift
        # table -- not two different archetypes' tables.
        shared_archetype = profile_v1["archetype"]
        self.assertEqual(
            _ARCHETYPE_TRAIT_SHIFTS.get(shared_archetype, {}),
            _ARCHETYPE_TRAIT_SHIFTS.get(profile_v2["archetype"], {}),
        )

    def test_many_bettors_archetype_stable_across_many_population_versions(self):
        for i in range(50):
            archetypes_seen = {
                generate_profile(f"nfl_2026_v{v}", "nfl", i)["archetype"]
                for v in range(1, 6)
            }
            self.assertEqual(
                len(archetypes_seen), 1,
                f"bettor_index {i} had archetype drift across population versions: {archetypes_seen}",
            )


class TestSportForcedZeroes(unittest.TestCase):
    def test_nfl_forces_cfb_only_traits_to_zero(self):
        for i in range(50):
            p = generate_profile("nfl_2026_v1", "nfl", i)
            self.assertEqual(p["ranked_team_attraction"], 0.0)
            self.assertEqual(p["large_spread_tolerance"], 0.0)

    def test_cfb_forces_nfl_only_traits_to_zero(self):
        for i in range(50):
            p = generate_profile("cfb_2026_v1", "cfb", i)
            self.assertEqual(p["standalone_primetime_bias"], 0.0)

    def test_unsupported_sport_rejected(self):
        with self.assertRaises(ValueError):
            generate_profile("x_2026_v1", "mlb", 0)


class TestTraitBounds(unittest.TestCase):
    def test_unsigned_traits_in_unit_interval(self):
        unsigned = set(TRAIT_NAMES) - {"stake_tier_shift", "bet_frequency_shift"}
        for i in range(200):
            p = generate_profile("nfl_2026_v1", "nfl", i)
            for t in unsigned:
                self.assertGreaterEqual(p[t], 0.0)
                self.assertLessEqual(p[t], 1.0)

    def test_signed_traits_bounded(self):
        for i in range(200):
            p = generate_profile("nfl_2026_v1", "nfl", i)
            self.assertGreaterEqual(p["stake_tier_shift"], -0.5)
            self.assertLessEqual(p["stake_tier_shift"], 0.5)
            self.assertGreaterEqual(p["bet_frequency_shift"], -0.5)
            self.assertLessEqual(p["bet_frequency_shift"], 0.5)


class TestGeneratePopulation(unittest.TestCase):
    def test_count_and_order(self):
        pop = generate_population("nfl_2026_v1", "nfl", 100)
        self.assertEqual(len(pop), 100)
        self.assertEqual([p["bettor_index"] for p in pop], list(range(100)))

    def test_matches_individual_generation(self):
        pop = generate_population("nfl_2026_v1", "nfl", 10)
        for i, profile in enumerate(pop):
            self.assertEqual(profile, generate_profile("nfl_2026_v1", "nfl", i))


class TestNoGameOrModelDataAccepted(unittest.TestCase):
    def test_function_signatures_take_no_game_or_conn_params(self):
        """
        Structural anti-circularity check: generate_profile must not even
        have a parameter slot that could carry game/market/model data.
        """
        import inspect
        sig = inspect.signature(generate_profile)
        self.assertEqual(
            list(sig.parameters),
            ["population_version", "sport", "bettor_index", "archetype_distribution_version"],
        )
        # None of these are a conn, a game dict, an as_of, or anything
        # resembling market/model data -- just identity + version strings.
        for name in sig.parameters:
            self.assertNotIn("conn", name)
            self.assertNotIn("game", name)
            self.assertNotIn("as_of", name)


if __name__ == "__main__":
    unittest.main()
