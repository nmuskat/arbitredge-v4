"""
tests/test_anti_circularity_simulation.py

Phase 1 anti-circularity enforcement. Static (AST-based) proof that the
simulation/ package never imports the evidence streams it exists to
independently corroborate, PLUS a runtime proof that passing any of
their output fields into bet_selection is refused.

This is deliberately a NEW test file introduced by Phase 1 -- this
production snapshot's existing test suite did not contain a prior
AST-based import-forbidding test to extend, so this establishes the
pattern for simulation/ rather than reusing one. Standalone-runnable:
python tests/test_anti_circularity_simulation.py
"""

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SIMULATION_DIR = REPO_ROOT / "simulation"

# Any import whose dotted path starts with one of these is forbidden
# anywhere in simulation/ -- these are exactly the evidence streams the
# synthetic bettor engine must independently corroborate, per the
# approved Phase 1 spec's non-negotiable input chain.
FORBIDDEN_IMPORT_PREFIXES = (
    "book_need.public_pressure",
    "book_need.inference",
    "book_need.synthetic_sportsbook",
    "book_need.aggregation",
    "book_need.need_strength",
    "book_need.synthetic_bettors",  # the legacy V2 module itself
    "behavioral",
)


def _imported_names(py_file: Path):
    tree = ast.parse(py_file.read_text(), filename=str(py_file))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


class TestNoForbiddenImportsInSimulationPackage(unittest.TestCase):
    def test_every_simulation_file_is_clean(self):
        py_files = sorted(SIMULATION_DIR.glob("*.py"))
        self.assertGreater(len(py_files), 0, "simulation/ package not found")

        violations = []
        for py_file in py_files:
            for imported in _imported_names(py_file):
                for forbidden in FORBIDDEN_IMPORT_PREFIXES:
                    if imported == forbidden or imported.startswith(forbidden + "."):
                        violations.append((py_file.name, imported))

        self.assertEqual(
            violations, [],
            f"simulation/ imported forbidden evidence-stream module(s): {violations}",
        )

    def test_bettor_profile_imports_only_its_own_config_sibling(self):
        """
        bettor_profile.py is a pure function of identity strings and an
        index -- it should import no project module that could carry
        game, market, or database access. The one legitimate exception
        is simulation.archetype_config: a plain, versioned data
        structure (archetype names + population percentages) with no
        conn, no game data, and no imports of its own outside the
        standard library -- verified separately below.
        """
        py_file = SIMULATION_DIR / "bettor_profile.py"
        project_top_level_packages = {
            p.name for p in REPO_ROOT.iterdir()
            if p.is_dir() and (p / "__init__.py").exists()
        }
        imported = _imported_names(py_file)
        project_imports = [
            name for name in imported
            if name.split(".")[0] in project_top_level_packages
        ]
        self.assertEqual(
            project_imports, ["simulation.archetype_config"],
            f"bettor_profile.py imported unexpected project modules: {project_imports}",
        )

    def test_archetype_config_is_pure_data_no_imports_at_all(self):
        """
        The one project module bettor_profile.py is allowed to import
        must itself be free of any import beyond the standard library --
        otherwise it could become a backdoor for a forbidden dependency.
        """
        py_file = SIMULATION_DIR / "archetype_config.py"
        imported = _imported_names(py_file)
        self.assertEqual(imported, [], f"archetype_config.py imported: {imported}")


class TestRuntimeGuardRejectsForbiddenFields(unittest.TestCase):
    """
    Complements the static AST check: even if a forbidden field somehow
    reached bet_selection at runtime (e.g. a caller upstream failed to
    strip it), the module refuses to proceed rather than silently using it.
    """

    def test_every_forbidden_key_individually_raises(self):
        from simulation.bet_selection import (
            game_interest_weight, CircularInputError, FORBIDDEN_GAME_FACT_KEYS,
        )
        from simulation.bettor_profile import generate_profile

        profile = generate_profile("nfl_2026_v1", "nfl", 0)
        base_facts = {"home_wins_last_3": 2, "away_wins_last_3": 1}

        self.assertGreater(len(FORBIDDEN_GAME_FACT_KEYS), 0)
        for key in FORBIDDEN_GAME_FACT_KEYS:
            with self.assertRaises(CircularInputError):
                game_interest_weight(profile, dict(base_facts, **{key: "x"}))

    def test_forbidden_key_list_covers_the_known_production_fields(self):
        """
        Pins the guard list against the exact field names confirmed, by
        direct inspection, to be the live circularity source in
        book_need/synthetic_bettors_v2.py's _public_side_probability()
        and book_need/run_slate_book_need.py's _shared_pool_game().
        """
        from simulation.bet_selection import FORBIDDEN_GAME_FACT_KEYS

        confirmed_production_fields = {
            "public_side",
            "side_preference_percentile",
            "side_preference_magnitude_percentile",
            "handle_intensity_percentile",
            "market_activity_percentile",
        }
        missing = confirmed_production_fields - FORBIDDEN_GAME_FACT_KEYS
        self.assertEqual(missing, set(), f"guard list is missing: {missing}")


if __name__ == "__main__":
    unittest.main()
