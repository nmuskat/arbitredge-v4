"""
simulation/archetype_config.py

Versioned archetype population configuration, kept separate from the
generation logic in bettor_profile.py so that a new population
percentage mix can be issued as a new config version without touching
any generation code.

UNVALIDATED_PRIOR: ARCHETYPE_DISTRIBUTION_V1 has no empirical backing.
It is a conservative, documented starting point, not a calibrated
estimate -- the _V1_UNVALIDATED_PRIOR naming must be preserved (or
carried forward explicitly) by any future version, until a version is
actually validated against something.

To add a new distribution: add a new ARCHETYPE_DISTRIBUTION_V2 dict
below (same keys as ARCHETYPES, summing to 1.0), add it to
DISTRIBUTIONS_BY_VERSION, and point ACTIVE_ARCHETYPE_DISTRIBUTION_VERSION
at it. Do not mutate V1 in place -- its whole point is to stay an
inspectable, frozen record of what Phase 1 shipped with.
"""

ARCHETYPES = (
    "CASUAL_FAVORITE",
    "CASUAL_BALANCED",
    "UNDERDOG_CONTRARIAN",
    "RECENCY_CHASER",
    "MARQUEE_GAME",
    "HIGH_STAKE_RECREATIONAL",
    "SELECTIVE_VALUE",
)

ARCHETYPE_DISTRIBUTION_V1_UNVALIDATED_PRIOR = {
    "CASUAL_FAVORITE": 0.25,
    "CASUAL_BALANCED": 0.25,
    "UNDERDOG_CONTRARIAN": 0.12,
    "RECENCY_CHASER": 0.12,
    "MARQUEE_GAME": 0.10,
    "HIGH_STAKE_RECREATIONAL": 0.06,
    "SELECTIVE_VALUE": 0.10,
}

# A second, MATERIALLY DIFFERENT distribution, registered but NOT
# active -- exists only so tests/test_bettor_profile.py's
# TestArchetypeIdentityImmutableAcrossDistributionChange can exercise
# the real "a new distribution version is available" mechanism without
# activating it for anyone. It is deliberately very different from V1
# (e.g. CASUAL_FAVORITE swings from 25% to 5%) so a test that failed to
# actually isolate an existing identity from it would be caught, not
# pass by coincidence of the two distributions being similar.
ARCHETYPE_DISTRIBUTION_V2_TEST_ONLY_NOT_ACTIVE = {
    "CASUAL_FAVORITE": 0.05,
    "CASUAL_BALANCED": 0.10,
    "UNDERDOG_CONTRARIAN": 0.35,
    "RECENCY_CHASER": 0.05,
    "MARQUEE_GAME": 0.05,
    "HIGH_STAKE_RECREATIONAL": 0.30,
    "SELECTIVE_VALUE": 0.10,
}

DISTRIBUTIONS_BY_VERSION = {
    "archetype_distribution_v1_unvalidated_prior": ARCHETYPE_DISTRIBUTION_V1_UNVALIDATED_PRIOR,
    "archetype_distribution_v2_test_only_not_active": ARCHETYPE_DISTRIBUTION_V2_TEST_ONLY_NOT_ACTIVE,
}

# The version actually in effect for new profile generation. Changing
# this is the ONLY step needed to roll out a new distribution -- no
# change to bettor_profile.py's generation logic. Still v1: the v2
# entry above exists only for the identity-immutability regression
# test, not as a rollout candidate.
ACTIVE_ARCHETYPE_DISTRIBUTION_VERSION = "archetype_distribution_v1_unvalidated_prior"


def _validate(version, distribution):
    if set(distribution) != set(ARCHETYPES):
        raise ValueError(f"{version}: keys must exactly match ARCHETYPES")
    total = sum(distribution.values())
    if abs(total - 1.0) >= 1e-9:
        raise ValueError(f"{version}: distribution must sum to 1.0, got {total}")


for _version, _dist in DISTRIBUTIONS_BY_VERSION.items():
    _validate(_version, _dist)


def get_archetype_distribution(version=None):
    """Returns (version, distribution_dict) for the requested (or active) version."""
    version = version or ACTIVE_ARCHETYPE_DISTRIBUTION_VERSION
    if version not in DISTRIBUTIONS_BY_VERSION:
        raise ValueError(f"unknown archetype distribution version: {version!r}")
    return version, DISTRIBUTIONS_BY_VERSION[version]
