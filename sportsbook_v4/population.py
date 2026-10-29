"""
sportsbook_v4/population.py -- persistent, deterministic bettor populations.

A population is fully determined by (sport, PARAMS_VERSION, bettor_count,
team_universe). It is written once, append-only, and its population_hash
lets any later reader verify that nothing was altered. Archetype, bankroll
and every trait are drawn from a per-bettor seed, so bettor k is the same
bettor in every run and every week.

Milestone-1 scope note: bankroll is the bettor's STARTING bankroll. Bankroll
evolution (settled results feeding back into later weeks, loss chasing)
is milestone 3 and will be event-sourced on top of this table, never by
rewriting it.
"""

import json
import math
import random
from datetime import datetime, timezone

from sportsbook_v4.params import ARCHETYPES, PARAMS_VERSION, TEAM_POPULARITY_PRIOR, params_hash
from sportsbook_v4.util import ISO_FMT, canonical_json, json_hash, seed_int, sha256_hex

DEFAULT_BETTOR_COUNT = 10_000
_TRAITS = ("unit_fraction", "arrivals_per_week", "fav_pref", "home_pref",
           "popularity_weight", "recency_weight")


class PopulationMismatch(RuntimeError):
    pass


def _clamped_normal(rng, spec):
    mean, sd, lo, hi = spec
    return max(lo, min(hi, rng.gauss(mean, sd) if sd > 0 else mean))


def _weighted_pick(rng, items):
    total = sum(w for _, w in items)
    target = rng.random() * total
    acc = 0.0
    for item, w in items:
        acc += w
        if target < acc:
            return item
    return items[-1][0]


def team_popularity(team_id):
    return float(TEAM_POPULARITY_PRIOR.get(team_id, 1.0))


def generate_bettor(sport, bettor_index, team_universe):
    rng = random.Random(seed_int("v4_bettor", sport, PARAMS_VERSION, bettor_index))
    archetype = _weighted_pick(rng, [(name, a["share"]) for name, a in sorted(ARCHETYPES.items())])
    a = ARCHETYPES[archetype]
    traits = {t: round(_clamped_normal(rng, a[t]), 6) for t in _TRAITS}
    bankroll = round(a["bankroll_median"] * math.exp(a["bankroll_log_sd"] * rng.gauss(0.0, 1.0)), 2)
    loyal_team = None
    if a["loyalty"] > 0 and team_universe:
        loyal_team = _weighted_pick(rng, [(t, team_popularity(t)) for t in sorted(team_universe)])
    traits["loyal_team"] = loyal_team
    rng_m3 = random.Random(seed_int("v4_bettor_m3", sport, PARAMS_VERSION, bettor_index))
    traits["over_pref"] = round(_clamped_normal(rng_m3, a["over_pref"]), 6)
    return {
        "bettor_index": bettor_index,
        "bettor_uid": sha256_hex("v4_bettor_uid", sport, PARAMS_VERSION, bettor_index)[:32],
        "archetype": archetype,
        "bankroll": bankroll,
        "traits": traits,
    }


def population_id_for(sport, bettor_count, team_universe):
    return f"{sport}|{PARAMS_VERSION}|n{bettor_count}|teams:{json_hash(sorted(team_universe))[:10]}"


_GENERATED = {}


def ensure_population(conn, sport, team_universe, bettor_count=DEFAULT_BETTOR_COUNT):
    """Idempotent. Returns population_id. Never rewrites an existing population."""
    if sport not in ("nfl", "cfb"):
        raise ValueError(f"unsupported sport {sport!r}")
    team_universe = sorted(set(team_universe))
    pid = population_id_for(sport, bettor_count, team_universe)
    row = conn.execute(
        "SELECT params_hash, bettor_count FROM v4_population WHERE population_id=?", (pid,)
    ).fetchone()
    if row is not None:
        if row[0] != params_hash():
            raise PopulationMismatch(
                f"population {pid} was created with params_hash {row[0]}, current is {params_hash()}"
            )
        return pid

    key = (pid, params_hash())     # deterministic in both (variants change parameters, not pid)
    if key not in _GENERATED:      # a replay into a fresh database reuses it
        _GENERATED[key] = [generate_bettor(sport, i, team_universe) for i in range(bettor_count)]
    bettors = _GENERATED[key]
    pop_hash = json_hash(bettors)
    now = datetime.now(timezone.utc).strftime(ISO_FMT)  # provenance only, never an input
    try:
        conn.execute("SAVEPOINT v4_pop")
        conn.execute(
            "INSERT INTO v4_population (population_id, sport, params_version, params_hash, bettor_count, "
            "team_universe, population_hash, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (pid, sport, PARAMS_VERSION, params_hash(), bettor_count, json.dumps(team_universe), pop_hash, now),
        )
        conn.executemany(
            "INSERT INTO v4_bettor (population_id, bettor_index, bettor_uid, archetype, bankroll, traits_json) "
            "VALUES (?,?,?,?,?,?)",
            [(pid, b["bettor_index"], b["bettor_uid"], b["archetype"], b["bankroll"], canonical_json(b["traits"]))
             for b in bettors],
        )
        conn.execute("RELEASE SAVEPOINT v4_pop")
        conn.commit()
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT v4_pop")
        conn.execute("RELEASE SAVEPOINT v4_pop")
        raise
    return pid


def load_population(conn, population_id, verify=True):
    meta = conn.execute(
        "SELECT sport, params_hash, bettor_count, population_hash FROM v4_population WHERE population_id=?",
        (population_id,),
    ).fetchone()
    if meta is None:
        raise KeyError(population_id)
    sport, phash, count, pop_hash = meta
    rows = conn.execute(
        "SELECT bettor_index, bettor_uid, archetype, bankroll, traits_json FROM v4_bettor "
        "WHERE population_id=? ORDER BY bettor_index",
        (population_id,),
    ).fetchall()
    bettors = [
        {"bettor_index": i, "bettor_uid": uid, "archetype": arch, "bankroll": br, "traits": json.loads(tj)}
        for i, uid, arch, br, tj in rows
    ]
    if verify:
        if len(bettors) != count or json_hash(bettors) != pop_hash:
            raise PopulationMismatch(f"population {population_id} failed integrity check")
    return {"population_id": population_id, "sport": sport, "params_hash": phash, "bettors": bettors}
