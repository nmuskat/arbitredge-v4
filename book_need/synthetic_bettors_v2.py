"""
ArbitrEdge Synthetic Bettor Population V2.

Models ONE shared population of synthetic sportsbook customers across
an NFL slate. Bettors select games, stakes, and sides from the same
shared population.

This is a behavioral simulation, not observed sportsbook betting data.
10,000 bettors is simulation resolution, not sportsbook customer count.

--------------------------------------------------------------------
PHASE 1 ADDITION -- SYNTHETIC_BETTOR_ENGINE feature flag, SHADOW ONLY
--------------------------------------------------------------------
simulate_shared_nfl_population() below is UNCHANGED from before Phase
1 -- same signature, same body, same output, always. It still feeds
book_need/run_slate_book_need.py's compute_inference(...,
synthetic_bettor_ledger=...) call, and that is deliberately the ONLY
thing it ever does. It has no SYNTHETIC_BETTOR_ENGINE branch and no
conn/as_of parameters, so there is no way for the V3 engine to reach
compute_inference's legacy-shaped ledger contract through this
function, by flag, by accident, or otherwise.

run_shadow_v3_population() is a SEPARATE, new, Phase 1 function. It is
the only way to invoke simulation.population.run_simulation (the
persistent-identity / archetype-driven V3 engine) from this module.
Its output -- favorite/underdog buckets, never public/other -- must
NEVER be passed into compute_inference's synthetic_bettor_ledger
argument: that argument feeds book_need.synthetic_sportsbook's
evaluate_synthetic_book(), which is written around the legacy
public_tickets/other_dollars/exposure_score shape, and would silently
misinterpret a favorite/underdog dict rather than erroring on it. Per
the approved Phase 1 plan, V3 stays a SHADOW output for inspection
only until Phase 2 replaces the downstream sportsbook accounting with
real monetary-ledger math. See
tests/test_shadow_boundary.py for the enforcement test and
book_need/run_slate_book_need.py for how the shadow call is kept
structurally separate from the legacy call that feeds compute_inference.
"""

import hashlib
import math
import os
import random

VERSION = "synthetic_bettors_v2_shared_pool"
DEFAULT_BETTORS = 10_000

SYNTHETIC_BETTOR_ENGINE_ENV_VAR = "SYNTHETIC_BETTOR_ENGINE"
LEGACY_ENGINE = "v2_legacy"
V3_ENGINE = "v3_persistent"


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _stable_seed(slate_key, bettor_count):
    raw = f"{slate_key}|{bettor_count}|{VERSION}"
    return int(hashlib.sha256(raw.encode()).hexdigest()[:16], 16)


def _weighted_choice(rng, items, weights):
    total = sum(weights)
    if total <= 0:
        return rng.choice(items)

    target = rng.random() * total
    running = 0.0

    for item, weight in zip(items, weights):
        running += weight
        if target <= running:
            return item

    return items[-1]


def _stake(rng):
    u = rng.random()
    if u < 0.72:
        return rng.uniform(20, 125)
    if u < 0.94:
        return rng.uniform(125, 500)
    if u < 0.992:
        return rng.uniform(500, 2000)
    return rng.uniform(2000, 7500)


def _wagers_for_bettor(rng):
    u = rng.random()
    if u < 0.28:
        return 0
    if u < 0.67:
        return 1
    if u < 0.87:
        return 2
    if u < 0.96:
        return 3
    if u < 0.99:
        return 4
    return 5


def _game_interest_weight(game):
    """
    Relative probability that a synthetic NFL wager lands on this game.
    Market activity is one input, not a measurement of actual handle.
    """
    weight = 1.0

    competing = game.get("competing_games_same_window_same_sport")
    if competing is not None:
        competing = max(0, int(competing))
        weight *= 1.0 / math.sqrt(1.0 + 0.12 * competing)

    if game.get("is_standalone_same_sport") == 1:
        weight *= 1.45

    network = str(game.get("tv_network") or "").upper()
    if network and any(
        x in network for x in ("NBC", "ESPN", "ABC", "FOX", "CBS", "PRIME", "AMAZON")
    ):
        weight *= 1.12

    if game.get("ranking_home") is not None or game.get("ranking_away") is not None:
        weight *= 1.05

    return max(0.05, weight)


def _public_side_probability(game):
    """
    Convert relative recreational-side evidence into a bounded probability.
    Side Preference percentile is not treated as an observed ticket share.
    """
    side = game.get("public_side")
    if side not in ("home", "away"):
        return None

    sp = game.get("side_preference_percentile")
    if sp is None:
        return 0.50

    sp = _clamp(float(sp), 0.0, 100.0)
    return _clamp(0.50 + 0.18 * (sp / 100.0), 0.50, 0.68)


def simulate_shared_nfl_population(games, slate_key, bettor_count=DEFAULT_BETTORS):
    """
    Simulate one shared NFL bettor population across all supplied games.

    UNCHANGED from before Phase 1: same signature, same body, same
    output. This function has no SYNTHETIC_BETTOR_ENGINE branch and no
    database access of any kind -- see run_shadow_v3_population() below
    for the (structurally separate) V3 shadow path.
    """
    games = [
        dict(game)
        for game in games
        if game.get("canonical_game_id") is not None
    ]

    if not games:
        return {
            "version": VERSION,
            "slate_key": slate_key,
            "population_size": int(bettor_count),
            "active_bettors": 0,
            "total_wagers": 0,
            "total_handle": 0.0,
            "games": {},
        }

    rng = random.Random(_stable_seed(slate_key, bettor_count))

    ledgers = {}
    for game in games:
        gid = game["canonical_game_id"]
        public_side = game.get("public_side")
        ledgers[gid] = {
            "version": VERSION,
            "canonical_game_id": gid,
            "public_side": public_side,
            "book_need_side": (
                "away" if public_side == "home"
                else "home" if public_side == "away"
                else None
            ),
            "public_tickets": 0,
            "other_tickets": 0,
            "public_dollars": 0.0,
            "other_dollars": 0.0,
        }

    game_weights = [_game_interest_weight(game) for game in games]

    active_bettors = 0
    total_wagers = 0
    total_handle = 0.0

    for _ in range(int(bettor_count)):
        wager_count = _wagers_for_bettor(rng)
        if wager_count <= 0:
            continue

        active_bettors += 1

        for _ in range(wager_count):
            game = _weighted_choice(rng, games, game_weights)
            gid = game["canonical_game_id"]
            ledger = ledgers[gid]

            stake = _stake(rng)
            public_prob = _public_side_probability(game)
            takes_public = rng.random() < (0.50 if public_prob is None else public_prob)

            if takes_public:
                ledger["public_tickets"] += 1
                ledger["public_dollars"] += stake
            else:
                ledger["other_tickets"] += 1
                ledger["other_dollars"] += stake

            total_wagers += 1
            total_handle += stake

    for gid, ledger in ledgers.items():
        tickets = ledger["public_tickets"] + ledger["other_tickets"]
        dollars = ledger["public_dollars"] + ledger["other_dollars"]

        public_ticket_share = (
            100.0 * ledger["public_tickets"] / tickets if tickets else None
        )
        public_money_share = (
            100.0 * ledger["public_dollars"] / dollars if dollars else None
        )

        if public_ticket_share is not None and public_money_share is not None:
            ticket_imbalance = abs(public_ticket_share - 50.0) * 2.0
            money_imbalance = abs(public_money_share - 50.0) * 2.0
            exposure_score = 0.40 * ticket_imbalance + 0.60 * money_imbalance
        else:
            exposure_score = None

        ledger["bettor_count"] = tickets
        ledger["synthetic_ticket_count"] = tickets
        ledger["synthetic_handle"] = round(dollars, 2)
        ledger["public_ticket_share"] = (
            round(public_ticket_share, 1) if public_ticket_share is not None else None
        )
        ledger["public_money_share"] = (
            round(public_money_share, 1) if public_money_share is not None else None
        )
        ledger["ticket_share_of_slate"] = (
            round(100.0 * tickets / total_wagers, 2) if total_wagers else 0.0
        )
        ledger["handle_share_of_slate"] = (
            round(100.0 * dollars / total_handle, 2) if total_handle else 0.0
        )
        ledger["exposure_score"] = (
            round(exposure_score, 1) if exposure_score is not None else None
        )
        ledger["public_dollars"] = round(ledger["public_dollars"], 2)
        ledger["other_dollars"] = round(ledger["other_dollars"], 2)

    return {
        "version": VERSION,
        "slate_key": slate_key,
        "population_size": int(bettor_count),
        "active_bettors": active_bettors,
        "total_wagers": total_wagers,
        "total_handle": round(total_handle, 2),
        "games": ledgers,
    }


def run_shadow_v3_population(
    games,
    conn,
    as_of,
    sport="nfl",
    bettor_count=DEFAULT_BETTORS,
    simulation_run_key=None,
    write_wagers=False,
    read_only=False,
):
    """
    SHADOW PATH -- the ONLY way to invoke the V3 persistent-identity
    engine (simulation/ package) from this module.

    Returns None whenever SYNTHETIC_BETTOR_ENGINE != "v3_persistent",
    so a caller can invoke this unconditionally on every slate and
    simply ignore a None result -- there is no separate flag check the
    caller needs to get right.

    Returns simulation.population.run_simulation's output UNCHANGED
    when the flag is on: a real, home/away-oriented monetary ledger
    (Phase 2A), never relabeled as public/other/book_need_side. That
    relabeling would be a lie -- V3 has no legitimate "public side"
    concept (see bet_selection.py's anti-circularity guard) -- so this
    function does not attempt it.

    simulation_run_key: Phase 2A's stable run identity (see
    simulation/run_identity.py), separate from as_of. Defaults to None,
    which simulation.population.run_simulation treats as "fall back to
    as_of" for backward compatibility -- but book_need/run_slate_book_need.py
    always passes an explicit, as_of-independent key built from the
    slate's betting day via run_identity.build_simulation_run_key, so
    that re-running the same day's orchestration later (as production
    does, on no fixed schedule, with as_of ticking forward each time)
    reproduces the same bettor decisions instead of drawing a fresh,
    structurally "new" set every call.

    write_wagers: forwarded to run_simulation, default False -- a
    shadow call never mutates the database unless a caller explicitly
    opts in (e.g. a dedicated Phase 2A persistence job, never the
    default book_need/run_slate_book_need.py shadow path today).

    CALLERS MUST NOT pass this return value into
    compute_inference(..., synthetic_bettor_ledger=...). That
    parameter feeds book_need.synthetic_sportsbook.evaluate_synthetic_book(),
    written for the legacy public_tickets/other_dollars/exposure_score
    shape and its liability_score -- it would silently misread this
    ledger's shape, and even if it didn't, V3's ledger-implied Need
    (simulation/ledger.py) is a deliberately separate, dollar-accurate
    concept from the legacy hand-weighted liability_score and must
    never be substituted for it. V3 stays a SHADOW output for
    inspection/storage only, per the approved Phase 1/2A plan. See
    tests/test_shadow_boundary.py.
    """
    engine = os.environ.get(SYNTHETIC_BETTOR_ENGINE_ENV_VAR, LEGACY_ENGINE)
    if engine != V3_ENGINE:
        return None

    from simulation.population import run_simulation
    from simulation.bet_selection import build_raw_game_facts

    # Explicit allowlist adapter (NOT "everything except the forbidden
    # keys"): only named raw facts cross into the V3 engine. Anything
    # else the legacy pool-game dict carries -- known-forbidden today,
    # or simply not yet named -- is dropped by construction.
    v3_games = [
        dict(build_raw_game_facts(game), canonical_game_id=game["canonical_game_id"])
        for game in games
        if game.get("canonical_game_id") is not None
    ]

    from simulation.run_identity import default_population_version
    population_version = default_population_version(sport)
    return run_simulation(
        conn, population_version, sport, v3_games, as_of, bettor_count,
        simulation_run_key=simulation_run_key, write_wagers=write_wagers, read_only=read_only,
    )
