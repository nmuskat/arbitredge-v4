"""
simulation/bettor_profile.py

Deterministic synthetic-bettor identity and profile generation for
ArbitrEdge V3 Phase 1.

IDENTITY vs CALIBRATION -- lifecycle (R3)
-------------------------------------------
Archetype assignment happens EXACTLY ONCE per (sport, bettor_index): at
the moment a brand-new synthetic_bettor_identity row is created. From
that point on, the archetype is loaded from the stored identity row
and is NEVER recomputed -- not by a new population_version, and not by
a new archetype_distribution_version either (that was R2's residual
bug: assign_archetype() still took archetype_distribution_version as
an input, so activating a new distribution version could, in
principle, recompute a different archetype for an EXISTING bettor_id).

    NEW BETTOR:
        sport + bettor_index
          -> assign_archetype(...) using the CURRENTLY ACTIVE
             archetype_distribution_version
          -> persist immutable synthetic_bettor_identity (see
             simulation/population.py's ensure_population)

    EXISTING BETTOR:
        load synthetic_bettor_identity.archetype from the database
          -> generate_traits_for_archetype(population_version, sport,
             bettor_index, that_stored_archetype)
          -> archetype is NEVER redrawn, whatever the active
             distribution version is now

assign_archetype() is therefore ONLY ever called by population.py at
identity-creation time for a bettor_index that does not yet have an
identity row. generate_traits_for_archetype() is the function used for
every other trait (re)generation -- it takes the archetype as a
parameter, with no distribution dependency of any kind, so it is
structurally incapable of redrawing it.

generate_profile() remains as a standalone convenience (used directly
by the test suite and by ensure_population only for brand-new
bettors): it assigns a fresh archetype AND generates traits in one
call. It must not be used by any code path that already has a stored
identity -- see population.py's ensure_population for the split.

A bettor's continuous behavioral trait VALUES remain versioned by
profile_version / population_version, so a future recalibration can
issue new trait values for the SAME identity without rewriting or
deleting history (population.py's tables are append-only) -- but those
new traits are always generated for that bettor's one true, archetype
that was fixed at identity creation.

ANTI-CIRCULARITY (structural, not just conventional)
-----------------------------------------------------
This module has NO access to any game, market, or model data at all --
it takes no conn, no game dict, no as_of. It imports nothing from
book_need or behavioral. A profile is a pure function of identity
strings and an index only. It cannot read Side Preference, Handle
Intensity, Public Pressure, split consensus, market response regime,
or Book Need even if someone tried to pass them in -- there is no
parameter slot for them.

DETERMINISM
------------
No wall-clock input anywhere in this module. The same inputs always
produce the exact same archetype and/or trait values, forever.
"""

import hashlib
import random

from simulation.archetype_config import (
    ARCHETYPES,
    get_archetype_distribution,
)

IDENTITY_GENERATOR_VERSION = "synthetic_bettor_identity_v1"
PROFILE_GENERATOR_VERSION = "synthetic_bettor_profile_v1"

# Traits in [0, 1] unless noted; see generate_traits_for_archetype() for
# sport-forced zeroes (a trait that does not apply to a sport is forced
# to exactly 0.0, not merely drawn small, so it is auditable as
# "inapplicable").
TRAIT_NAMES = (
    "favorite_bias",
    "underdog_contrarian_bias",
    "home_team_bias",
    "recency_sensitivity",
    "rest_sensitivity",
    "standalone_primetime_bias",
    "ranked_team_attraction",       # CFB-only; forced 0.0 for NFL
    "large_spread_tolerance",       # CFB-only; forced 0.0 for NFL
    "stake_tier_shift",             # signed, [-0.5, 0.5]
    "bet_frequency_shift",          # signed, [-0.5, 0.5]
)

_SIGNED_TRAITS = frozenset({"stake_tier_shift", "bet_frequency_shift"})

# Archetype -> additive trait-center shifts. Small nudges on top of a
# continuous population draw -- never a hard assignment, and every
# trait still varies within an archetype.
_ARCHETYPE_TRAIT_SHIFTS = {
    "CASUAL_FAVORITE": {"favorite_bias": 0.20, "underdog_contrarian_bias": -0.15},
    "CASUAL_BALANCED": {},
    "UNDERDOG_CONTRARIAN": {"underdog_contrarian_bias": 0.20, "favorite_bias": -0.15},
    "RECENCY_CHASER": {"recency_sensitivity": 0.30, "bet_frequency_shift": 0.08},
    "MARQUEE_GAME": {"standalone_primetime_bias": 0.30, "ranked_team_attraction": 0.30},
    "HIGH_STAKE_RECREATIONAL": {"stake_tier_shift": 0.12},
    "SELECTIVE_VALUE": {"bet_frequency_shift": -0.08, "rest_sensitivity": 0.15},
}


def _clamp01(x):
    return max(0.0, min(1.0, x))


def _digest_seed(*parts):
    raw = "|".join(str(p) for p in parts)
    return int(hashlib.sha256(raw.encode()).hexdigest()[:16], 16)


def _weighted_choice(rng, items_with_weights):
    total = sum(w for _, w in items_with_weights)
    target = rng.random() * total
    running = 0.0
    for item, weight in items_with_weights:
        running += weight
        if target <= running:
            return item
    return items_with_weights[-1][0]


def assign_archetype(sport, bettor_index, archetype_distribution_version=None):
    """
    Deterministic archetype draw -- IDENTITY-CREATION ONLY. Call this
    exactly once, when a brand-new (sport, bettor_index) identity is
    about to be persisted for the first time (see
    population.ensure_population). Never call this for a bettor_index
    that already has a synthetic_bettor_identity row -- load its
    stored archetype instead and pass it to
    generate_traits_for_archetype().

    Pure function of (sport, bettor_index, IDENTITY_GENERATOR_VERSION,
    archetype_distribution_version). Deliberately does NOT depend on
    population_version.
    """
    version, distribution = get_archetype_distribution(archetype_distribution_version)
    seed = _digest_seed(sport, bettor_index, IDENTITY_GENERATOR_VERSION, version, "archetype")
    rng = random.Random(seed)
    return _weighted_choice(rng, list(distribution.items())), version


def generate_traits_for_archetype(population_version, sport, bettor_index, archetype):
    """
    Deterministically generate ONLY the continuous trait values for a
    bettor whose archetype is ALREADY KNOWN (loaded from a stored
    synthetic_bettor_identity row, or freshly assigned by
    generate_profile() for a brand-new bettor). This function never
    assigns or re-derives an archetype -- it takes one as a plain
    parameter and has no archetype_distribution_version input of any
    kind, so it is structurally incapable of redrawing identity.

    Same (population_version, sport, bettor_index, archetype) -> same
    trait values, forever. No as_of, no wall-clock time, no game/market
    data anywhere in this call.
    """
    if sport not in ("nfl", "cfb"):
        raise ValueError(f"unsupported sport: {sport!r}")
    if bettor_index < 0:
        raise ValueError("bettor_index must be >= 0")
    if archetype not in ARCHETYPES:
        raise ValueError(f"unknown archetype: {archetype!r}")

    seed = _digest_seed(population_version, sport, bettor_index, PROFILE_GENERATOR_VERSION, "traits")
    rng = random.Random(seed)

    shifts = _ARCHETYPE_TRAIT_SHIFTS.get(archetype, {})

    traits = {}
    for name in TRAIT_NAMES:
        if name in _SIGNED_TRAITS:
            base = rng.gauss(0.0, 0.15 if name == "stake_tier_shift" else 0.10)
        else:
            base = rng.betavariate(2, 2)

        value = base + shifts.get(name, 0.0)
        traits[name] = (
            max(-0.5, min(0.5, value)) if name in _SIGNED_TRAITS else _clamp01(value)
        )

    # Sport-inapplicable traits are forced to exactly zero, auditable as
    # "does not apply," not merely small. bet_selection.py must treat
    # these as structurally neutral, not as "zero tolerance" or any
    # other real behavioral value -- see its sport-gating.
    if sport == "nfl":
        traits["ranked_team_attraction"] = 0.0
        traits["large_spread_tolerance"] = 0.0
    else:  # cfb
        traits["standalone_primetime_bias"] = 0.0

    return {
        "population_version": population_version,
        "profile_version": PROFILE_GENERATOR_VERSION,
        "identity_generator_version": IDENTITY_GENERATOR_VERSION,
        "sport": sport,
        "bettor_index": bettor_index,
        "archetype": archetype,
        **traits,
    }


def generate_profile(population_version, sport, bettor_index, archetype_distribution_version=None):
    """
    Convenience for a BRAND-NEW bettor only: assigns a fresh archetype
    (assign_archetype) and generates its traits
    (generate_traits_for_archetype) in one call. Returns the same shape
    as generate_traits_for_archetype, plus archetype_distribution_version.

    Do not call this for a bettor_index that already has a persisted
    identity -- that would redraw its archetype from whatever
    distribution version happens to be active right now, which is
    exactly the bug this module exists to prevent. population.py's
    ensure_population() only calls this for bettor_index values with no
    existing synthetic_bettor_identity row; for existing rows it loads
    the stored archetype and calls generate_traits_for_archetype directly.
    """
    archetype, resolved_distribution_version = assign_archetype(
        sport, bettor_index, archetype_distribution_version
    )
    profile = generate_traits_for_archetype(population_version, sport, bettor_index, archetype)
    profile["archetype_distribution_version"] = resolved_distribution_version
    return profile


def generate_population(population_version, sport, bettor_count):
    """Generate a full population's profiles in bettor_index order
    (brand-new-bettor convenience; see generate_profile's docstring)."""
    return [generate_profile(population_version, sport, i) for i in range(bettor_count)]
