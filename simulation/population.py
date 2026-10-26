"""
simulation/population.py

Orchestrates persistent synthetic-bettor populations: idempotent
creation, profile retrieval, and running one slate's worth of wager
generation through bet_selection.py + offered_line.py.

SCHEMA (append-only; see APPEND_ONLY_TRIGGERS_SQL)
-----------------------------------------------------
synthetic_bettor_identity -- immutable identity: sport, bettor_index,
    archetype, and archetype_distribution_version (provenance only --
    see _get_or_create_identity). Never updated or deleted once
    written. archetype is decided EXACTLY ONCE, here, when the row is
    first created -- see bettor_profile.py's module docstring for the
    full identity-vs-calibration lifecycle (R3).
synthetic_bettor_profile -- versioned calibration: continuous trait
    values keyed by (bettor_id, population_version). A recalibration
    issues a NEW population_version row rather than rewriting this
    one, and is always generated from that bettor's STORED archetype
    (generate_traits_for_archetype), never a freshly drawn one.
synthetic_wager -- forward-looking append-only INDIVIDUAL wager
    ledger. Phase 1 defined this table (and the triggers that make it
    append-only) but never wrote to it. Phase 2A is the first phase
    that persists real rows here, gated by run_simulation's
    write_wagers parameter (default False -- shadow calls never
    mutate the database unless a caller explicitly opts in). Phase 2A
    additively migrates in `wager_ordinal` (see _migrate_wager_ordinal
    below) and a UNIQUE index on (bettor_id, simulation_run_id,
    wager_ordinal) -- the deterministic identity of one wager within
    one run -- so a replayed run idempotently no-ops on rows it
    already wrote rather than duplicating them, and so a genuine
    content mismatch under that same identity fails loudly (see
    _persist_wagers) rather than silently keeping stale or
    inconsistent data. side allows both spread (home/away) and future
    totals (over/under) sides, with a compound CHECK tying each to its
    market, so totals do not require a schema change later.

DETERMINISM
------------
A brand-new bettor's archetype is assigned once via
bettor_profile.assign_archetype() (sport, bettor_index, and the active
archetype_distribution_version at creation time only). Every other
trait (re)generation goes through
bettor_profile.generate_traits_for_archetype(), whose inputs are
(population_version, sport, bettor_index, archetype) -- no as_of, and
no distribution dependency of any kind.

Phase 2A (run_identity.py): per-bettor wager-selection RNG is seeded
from simulation_run_key -- a STABLE identity for "this slate's run",
explicit and caller-supplied -- NOT from as_of. as_of remains purely
the market-observation cutoff that controls which offered line/price a
bettor sees (via offered_line.construct_offered_line); it no longer
also doubles as the randomness identity. This means re-invoking the
same logical run (same simulation_run_key) later the same betting day,
with as_of having ticked forward because production re-runs
orchestration on no fixed schedule, reproduces the same sequence of
bettor decisions -- only a genuine change in the offered market data
changes what a bettor's probability model computes, never a trivial
as_of timestamp difference alone. No wall-clock-seeded randomness
(datetime.now(), uuid4()) anywhere in this module or in run_identity.py.
"""

import datetime
import random

from simulation.bettor_profile import assign_archetype, generate_traits_for_archetype
from simulation.bet_selection import (
    select_game,
    select_spread_side,
    select_total_side,
    select_stake,
    select_wager_count,
    build_raw_game_facts,
)
from simulation.offered_line import construct_offered_line, construct_offered_total
from simulation.ledger import build_game_ledger, build_total_ledger
from simulation.run_identity import compute_simulation_run_id, wager_rng_seed, total_wager_rng_seed

DEFAULT_BETTOR_COUNT = 10_000
TOTAL_WAGER_ORDINAL_BASE = 1_000_000
VERSION = "synthetic_bettors_v3_persistent"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS synthetic_bettor_identity (
    bettor_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    sport        TEXT NOT NULL CHECK (sport IN ('nfl','cfb')),
    bettor_index INTEGER NOT NULL,
    archetype    TEXT NOT NULL,
    -- Recorded ONLY as provenance (which distribution was active when
    -- this identity was created). Never read back to decide or
    -- re-decide an archetype -- archetype itself is the single source
    -- of truth for every later trait (re)generation. See
    -- bettor_profile.py's module docstring for the full lifecycle.
    archetype_distribution_version TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    UNIQUE (sport, bettor_index)
);

CREATE TABLE IF NOT EXISTS synthetic_bettor_profile (
    profile_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    bettor_id                  INTEGER NOT NULL REFERENCES synthetic_bettor_identity(bettor_id),
    population_version         TEXT NOT NULL,
    profile_version            TEXT NOT NULL,
    -- archetype_distribution_version lives on synthetic_bettor_identity
    -- now (R3): it is provenance for how the IDENTITY was created, not
    -- a per-calibration input, so it has no business on this table.
    favorite_bias               REAL NOT NULL,
    underdog_contrarian_bias    REAL NOT NULL,
    home_team_bias              REAL NOT NULL,
    recency_sensitivity         REAL NOT NULL,
    rest_sensitivity            REAL NOT NULL,
    standalone_primetime_bias   REAL NOT NULL,
    ranked_team_attraction      REAL NOT NULL,
    large_spread_tolerance      REAL NOT NULL,
    stake_tier_shift             REAL NOT NULL,
    bet_frequency_shift          REAL NOT NULL,
    created_at                    TEXT NOT NULL,
    UNIQUE (bettor_id, population_version)
);

CREATE TABLE IF NOT EXISTS synthetic_wager (
    wager_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    bettor_id               INTEGER NOT NULL REFERENCES synthetic_bettor_identity(bettor_id),
    population_version      TEXT NOT NULL,
    canonical_game_id        TEXT NOT NULL,
    sport                     TEXT NOT NULL,
    market                     TEXT NOT NULL CHECK (market IN ('spread','total')),
    -- side supports both spread sides (home/away) and future totals
    -- sides (over/under) so Phase 2 totals does not require a schema
    -- redesign. The compound CHECK additionally enforces that the
    -- side is valid FOR the stated market (spread -> home/away,
    -- total -> over/under) -- a spread/over or total/home row is
    -- rejected at the database level, not just by convention.
    side                        TEXT NOT NULL CHECK (side IN ('home','away','over','under')),
    stake                        REAL NOT NULL,
    accepted_line                 REAL NOT NULL,
    accepted_price                 REAL,
    reference_books_used            TEXT NOT NULL,
    as_of_decision                   TEXT NOT NULL,
    accepted_at                       TEXT NOT NULL,
    simulation_run_id                  TEXT NOT NULL,
    -- Phase 2A: this wager's 0-based position among the wagers THIS
    -- bettor actually placed within THIS simulation_run_id (not the
    -- attempt count -- a skipped attempt, e.g. no legitimate price for
    -- the selected side, never consumes an ordinal). Together with
    -- bettor_id and simulation_run_id this is the wager's deterministic
    -- identity -- see the UNIQUE index below and _persist_wagers.
    wager_ordinal                      INTEGER NOT NULL,
    CHECK (
        (market = 'spread' AND side IN ('home','away'))
        OR
        (market = 'total' AND side IN ('over','under'))
    )
);
"""

# Phase 2A deterministic-identity index. Created separately from
# SCHEMA_SQL's CREATE TABLE (rather than inline) so the same
# CREATE INDEX IF NOT EXISTS statement can also be run, by
# _migrate_wager_ordinal, against a pre-Phase-2A database that already
# has a synthetic_wager table but not yet this index or column.
WAGER_IDENTITY_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_synthetic_wager_identity
    ON synthetic_wager (bettor_id, simulation_run_id, wager_ordinal);
"""

# R2 (migration review): SQLite's UNIQUE index allows any number of
# NULL wager_ordinal rows to coexist (NULL is never considered equal
# to NULL for uniqueness purposes) -- so the index above alone does
# NOT stop a NEW row from being inserted with a NULL ordinal. A
# column-level NOT NULL can't be retrofitted via ALTER TABLE here
# without potentially rejecting pre-existing legacy rows that might
# already carry a NULL (there are none in any known production
# database -- Phase 1 never wrote to this table -- but the table
# could in principle have been touched by something else before
# Phase 2A, and a destructive rewrite to "fix" that is explicitly out
# of scope).
#
# This trigger closes the gap WITHOUT touching existing rows: it only
# fires BEFORE INSERT, so it validates every NEW row going forward and
# never re-validates, locks out, or rejects anything already stored.
# Legacy rows with a NULL wager_ordinal (if any ever existed) are left
# alone -- by construction they are exempt from the idempotency/
# uniqueness scheme above (NULL never collides with anything), and
# simply fall outside Phase 2A's identity-tracked wager set. Every
# Phase-2A+ write, by contrast, is guaranteed non-null by this trigger
# -- the application path (population._persist_wagers) always supplies
# one, and this is the backstop that makes "always" a database-level
# guarantee rather than just a convention.
WAGER_ORDINAL_REQUIRED_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_synthetic_wager_ordinal_required
    BEFORE INSERT ON synthetic_wager
    WHEN NEW.wager_ordinal IS NULL
    BEGIN SELECT RAISE(ABORT, 'synthetic_wager.wager_ordinal must not be NULL for a new row'); END;
"""

APPEND_ONLY_TRIGGERS_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_synthetic_bettor_identity_no_update
    BEFORE UPDATE ON synthetic_bettor_identity
    BEGIN SELECT RAISE(ABORT, 'synthetic_bettor_identity is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_synthetic_bettor_identity_no_delete
    BEFORE DELETE ON synthetic_bettor_identity
    BEGIN SELECT RAISE(ABORT, 'synthetic_bettor_identity is append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_synthetic_bettor_profile_no_update
    BEFORE UPDATE ON synthetic_bettor_profile
    BEGIN SELECT RAISE(ABORT, 'synthetic_bettor_profile is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_synthetic_bettor_profile_no_delete
    BEFORE DELETE ON synthetic_bettor_profile
    BEGIN SELECT RAISE(ABORT, 'synthetic_bettor_profile is append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_synthetic_wager_no_update
    BEFORE UPDATE ON synthetic_wager
    BEGIN SELECT RAISE(ABORT, 'synthetic_wager is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_synthetic_wager_no_delete
    BEFORE DELETE ON synthetic_wager
    BEGIN SELECT RAISE(ABORT, 'synthetic_wager is append-only'); END;
"""


def _migrate_wager_ordinal(conn):
    """
    Additive, non-destructive migration for a DATABASE THAT ALREADY HAS
    a synthetic_wager table from before Phase 2A (which never had a
    wager_ordinal column, since nothing wrote to the table yet). Safe
    to run against an empty table (the production case -- Phase 1
    never persisted wagers) or a populated one: it only ADDS a column
    and an index, never drops or rewrites a table, and is itself
    idempotent (re-running it is a no-op).

    CREATE TABLE IF NOT EXISTS in SCHEMA_SQL already gives a brand-new
    database the column directly; this function exists purely for an
    existing database whose synthetic_wager table predates it.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(synthetic_wager)").fetchall()}
    if not cols:
        return  # table doesn't exist yet -- SCHEMA_SQL's CREATE TABLE below handles it
    if "wager_ordinal" not in cols:
        # No NOT NULL/CHECK can be added via ALTER TABLE ADD COLUMN in
        # SQLite when the table may already hold rows, so this adds it
        # nullable; Phase 2A's own writes always populate it, and the
        # uniqueness index below is what actually enforces the
        # deterministic-identity guarantee for rows written from here on.
        conn.execute("ALTER TABLE synthetic_wager ADD COLUMN wager_ordinal INTEGER")
    conn.execute(WAGER_IDENTITY_INDEX_SQL)
    conn.execute(WAGER_ORDINAL_REQUIRED_TRIGGER_SQL)


def ensure_schema(conn):
    conn.executescript(SCHEMA_SQL)
    _migrate_wager_ordinal(conn)
    conn.executescript(APPEND_ONLY_TRIGGERS_SQL)
    conn.commit()


def _utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _get_or_create_identity(conn, sport, bettor_index, now, archetype_distribution_version=None):
    """
    R3 lifecycle, enforced here (the ONLY place assign_archetype() is
    ever called):

      - If a (sport, bettor_index) identity row already exists, its
        stored archetype is loaded and returned UNCHANGED. The
        currently active archetype_distribution_version is completely
        irrelevant to an existing bettor -- it is never consulted.
      - Only if no row exists yet is assign_archetype() called (using
        the active distribution version) and a brand-new, henceforth-
        immutable identity row persisted.

    Returns (bettor_id, archetype, created: bool).
    """
    row = conn.execute(
        "SELECT bettor_id, archetype FROM synthetic_bettor_identity WHERE sport=? AND bettor_index=?",
        (sport, bettor_index),
    ).fetchone()
    if row is not None:
        return row[0], row[1], False

    archetype, resolved_distribution_version = assign_archetype(
        sport, bettor_index, archetype_distribution_version
    )

    conn.execute(
        "INSERT OR IGNORE INTO synthetic_bettor_identity "
        "(sport, bettor_index, archetype, archetype_distribution_version, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (sport, bettor_index, archetype, resolved_distribution_version, now),
    )

    # Re-select rather than trust lastrowid: a concurrent writer could
    # have won the UNIQUE(sport, bettor_index) race via INSERT OR
    # IGNORE, in which case the authoritative row -- and its archetype
    # -- is whichever one actually landed first, not necessarily this
    # call's own (identical, since deterministic) draw.
    bettor_id, archetype = conn.execute(
        "SELECT bettor_id, archetype FROM synthetic_bettor_identity WHERE sport=? AND bettor_index=?",
        (sport, bettor_index),
    ).fetchone()
    return bettor_id, archetype, True


def ensure_population(conn, population_version, sport, bettor_count=DEFAULT_BETTOR_COUNT, archetype_distribution_version=None):
    """
    Idempotent: creates bettor_count identity+profile rows for this
    (sport, population_version) if they don't already exist.

    Never regenerates, overwrites, or alters an existing bettor's
    identity (sport, bettor_index, archetype) -- see
    _get_or_create_identity. An existing bettor's traits for a NEW
    population_version are generated by
    generate_traits_for_archetype(), conditioned on that bettor's
    STORED archetype -- the active archetype_distribution_version (or
    any distribution change since the bettor was created) has no
    effect on it whatsoever.

    Returns the number of NEW identity rows created by this call.
    """
    ensure_schema(conn)

    created = 0
    now = _utc_now_iso()

    for bettor_index in range(bettor_count):
        bettor_id, archetype, was_created = _get_or_create_identity(
            conn, sport, bettor_index, now, archetype_distribution_version
        )
        if was_created:
            created += 1

        traits = generate_traits_for_archetype(population_version, sport, bettor_index, archetype)

        conn.execute(
            "INSERT OR IGNORE INTO synthetic_bettor_profile "
            "(bettor_id, population_version, profile_version, "
            "favorite_bias, underdog_contrarian_bias, home_team_bias, recency_sensitivity, "
            "rest_sensitivity, standalone_primetime_bias, ranked_team_attraction, "
            "large_spread_tolerance, stake_tier_shift, bet_frequency_shift, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bettor_id, population_version, traits["profile_version"],
                traits["favorite_bias"], traits["underdog_contrarian_bias"],
                traits["home_team_bias"], traits["recency_sensitivity"],
                traits["rest_sensitivity"], traits["standalone_primetime_bias"],
                traits["ranked_team_attraction"], traits["large_spread_tolerance"],
                traits["stake_tier_shift"], traits["bet_frequency_shift"], now,
            ),
        )

    conn.commit()
    return created


def get_profiles(conn, population_version, sport):
    """
    Returns a list of dicts (bettor_id, bettor_index, archetype, sport,
    and all continuous traits) for every bettor in this (sport,
    population_version), ordered by bettor_index. `sport` is included
    so bet_selection can sport-gate CFB-only traits (e.g.
    large_spread_tolerance) to have zero effect for NFL bettors rather
    than reinterpreting their forced 0.0 as a real behavioral value.
    """
    rows = conn.execute(
        """
        SELECT i.bettor_id, i.bettor_index, i.archetype, i.sport,
               p.favorite_bias, p.underdog_contrarian_bias, p.home_team_bias,
               p.recency_sensitivity, p.rest_sensitivity, p.standalone_primetime_bias,
               p.ranked_team_attraction, p.large_spread_tolerance,
               p.stake_tier_shift, p.bet_frequency_shift
        FROM synthetic_bettor_identity i
        JOIN synthetic_bettor_profile p
          ON p.bettor_id = i.bettor_id AND p.population_version = ?
        WHERE i.sport = ?
        ORDER BY i.bettor_index ASC
        """,
        (population_version, sport),
    ).fetchall()

    return [
        {
            "bettor_id": r[0], "bettor_index": r[1], "archetype": r[2], "sport": r[3],
            "favorite_bias": r[4], "underdog_contrarian_bias": r[5],
            "home_team_bias": r[6], "recency_sensitivity": r[7],
            "rest_sensitivity": r[8], "standalone_primetime_bias": r[9],
            "ranked_team_attraction": r[10], "large_spread_tolerance": r[11],
            "stake_tier_shift": r[12], "bet_frequency_shift": r[13],
        }
        for r in rows
    ]


def _persist_wagers(conn, wagers):
    """
    Idempotent, append-only, ATOMIC persistence of one batch (one
    run_simulation call's worth of wagers). Each wager's identity is
    (bettor_id, simulation_run_id, wager_ordinal) -- enforced by
    WAGER_IDENTITY_INDEX_SQL. INSERT OR IGNORE makes a byte-identical
    replay of the same run a silent no-op (true idempotency: the same
    inputs always produce the same ordinal sequence, so a re-run's rows
    collide on identity and are simply skipped).

    PERSISTENCE SEMANTICS (R2): simulation_run_id is now stable across
    as_of (see run_identity.compute_simulation_run_id) -- so a later
    invocation of the SAME logical run, at a LATER market cutoff, is
    EXPECTED to recompute the same wager identities, and is legitimate,
    not an error. The first accepted wager's ECONOMIC terms under a
    given identity are therefore FROZEN, and that frozen-terms set is
    deliberately narrower than every column on the row:

      canonical_game_id, sport, market, side, stake, accepted_line,
      accepted_price, reference_books_used

    as_of_decision and accepted_at are NOT part of that comparison --
    they are provenance (when this wager was most recently confirmed),
    not economics, and a later same-run call legitimately carries a
    later as_of_decision/accepted_at even though it is re-deriving the
    IDENTICAL bet. Since synthetic_wager is append-only (no UPDATE
    path at all), the row's as_of_decision/accepted_at permanently
    reflect whichever invocation's INSERT actually won -- that's
    expected and fine; it does not change what bet was made.

      - An exact economic replay (same terms under the same identity,
        whatever as_of produced it) is a silent no-op -- idempotent.
      - A later attempt to persist the SAME identity with MATERIALLY
        DIFFERENT ECONOMIC terms (a different stake/line/price/side/
        game -- which can only happen from a genuine bug, since the
        RNG and offered-line selection are both deterministic per run)
        is refused: it is NOT duplicated, NOT used to overwrite the
        original row (synthetic_wager is append-only), and NOT
        silently ignored either. It raises RuntimeError.
    A shadow call (write_wagers=False) is unaffected by any of this --
    it never reaches this function, so recomputing inspection-only
    output at a later as_of never touches the database.

    ATOMICITY (R2 fix): this whole batch is one transaction. If ANY
    wager in the batch raises (an inconsistent-identity collision),
    every row this call inserted so far -- for this batch only -- is
    rolled back before the exception propagates, via a SAVEPOINT
    scoped to this function. Earlier-committed data (from a prior,
    separate, successful call) is untouched; only this batch's own
    not-yet-committed work is undone. Without this, a partially
    inserted batch could sit uncommitted on the connection and later
    be swept into an unrelated commit() made elsewhere on the same
    connection.
    """
    conn.execute("SAVEPOINT persist_wagers_batch")
    try:
        for w in wagers:
            conn.execute(
                "INSERT OR IGNORE INTO synthetic_wager "
                "(bettor_id, population_version, canonical_game_id, sport, market, side, "
                "stake, accepted_line, accepted_price, reference_books_used, as_of_decision, "
                "accepted_at, simulation_run_id, wager_ordinal) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    w["bettor_id"], w["population_version"], w["canonical_game_id"], w["sport"],
                    w["market"], w["side"], w["stake"], w["accepted_line"], w["accepted_price"],
                    w["reference_books_used"], w["as_of_decision"], w["accepted_at"],
                    w["simulation_run_id"], w["wager_ordinal"],
                ),
            )

            # ECONOMIC terms only -- see docstring. as_of_decision and
            # accepted_at are provenance, not part of the frozen
            # identity comparison: a later same-run call is expected to
            # carry a later as_of_decision/accepted_at even on an
            # otherwise-identical (idempotent) replay.
            stored = conn.execute(
                "SELECT canonical_game_id, sport, market, side, stake, accepted_line, "
                "accepted_price, reference_books_used "
                "FROM synthetic_wager WHERE bettor_id=? AND simulation_run_id=? AND wager_ordinal=?",
                (w["bettor_id"], w["simulation_run_id"], w["wager_ordinal"]),
            ).fetchone()

            expected = (
                str(w["canonical_game_id"]), w["sport"], w["market"], w["side"], w["stake"],
                w["accepted_line"], w["accepted_price"], w["reference_books_used"],
            )
            if stored is None or tuple(stored) != expected:
                raise RuntimeError(
                    f"synthetic_wager identity collision with inconsistent content "
                    f"(first-accepted wager terms are frozen under this identity): "
                    f"bettor_id={w['bettor_id']} simulation_run_id={w['simulation_run_id']} "
                    f"wager_ordinal={w['wager_ordinal']} -- stored={stored!r} expected={expected!r}"
                )
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT persist_wagers_batch")
        conn.execute("RELEASE SAVEPOINT persist_wagers_batch")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT persist_wagers_batch")
        conn.commit()


def run_simulation(
    conn,
    population_version,
    sport,
    games,
    as_of,
    bettor_count=DEFAULT_BETTOR_COUNT,
    simulation_run_key=None,
    write_wagers=False,
    read_only=False,
):
    """
    games: list of dicts, each with at least canonical_game_id plus raw
        engineered_features_v2-style keys. Only keys in
        bet_selection.ALLOWED_RAW_GAME_FACT_KEYS are used -- anything
        else the caller's dict carries (forbidden or simply unknown) is
        dropped by the allowlist before bet_selection ever sees it.

    as_of: the market-observation cutoff (what offered line/price each
        bettor sees). simulation_run_key: the STABLE run identity used
        to seed RNG and scope wager_ordinal -- see run_identity.py.
        Defaults to as_of itself ONLY for backward-compatible direct
        callers that don't yet pass one explicitly; any caller that
        cares about replay stability across a changing as_of (every
        production caller) MUST pass its own stable key (see
        run_identity.build_simulation_run_key and
        book_need/synthetic_bettors_v2.run_shadow_v3_population).

    write_wagers: False by default -- a shadow/inspection call never
        mutates the database. Only an explicit write_wagers=True
        persists individual wager rows via _persist_wagers (idempotent,
        append-only; see that function and tests/test_wager_idempotency.py).

    Returns a dict with a per-game, HOME/AWAY-oriented monetary ledger
    (simulation.ledger.build_game_ledger) plus the individual wager
    records themselves. favorite/underdog is derived PRESENTATION
    metadata only (from ArbitrEdge's own offered line, never from any
    forbidden Side-Preference-style field) -- the ledger itself is
    home/away oriented, per Phase 2A.
    """
    if simulation_run_key is None:
        simulation_run_key = as_of  # see docstring -- back-compat only

    if read_only and write_wagers:
        raise ValueError('Read-only mode forbids wager persistence')
    if not read_only:
        ensure_schema(conn)
    profiles = get_profiles(conn, population_version, sport)
    if read_only and len(profiles) != bettor_count:
        raise RuntimeError('Read-only mode requires complete existing population')
    if not profiles:
        ensure_population(conn, population_version, sport, bettor_count)
        profiles = get_profiles(conn, population_version, sport)

    # R2: as_of is deliberately NOT an input to simulation_run_id -- see
    # run_identity.compute_simulation_run_id's docstring. It remains
    # fully preserved per-wager via as_of_decision/accepted_at/
    # accepted_line/accepted_price/reference_books_used.
    simulation_run_id = compute_simulation_run_id(
        population_version, sport, simulation_run_key, bettor_count
    )

    games = [dict(g) for g in games if g.get("canonical_game_id") is not None]

    empty_result = {
        "version": VERSION,
        "population_version": population_version,
        "sport": sport,
        "simulation_run_id": simulation_run_id,
        "simulation_run_key": simulation_run_key,
        "as_of": as_of,
        "population_size": len(profiles),
        "active_bettors": 0,
        "total_wagers": 0,
        "total_handle": 0.0,
        "wagers": [],
        "games": {},
    }
    if not games:
        return empty_result

    candidate_games = []
    game_by_id = {}
    offered_lines = {}
    offered_totals = {}
    for game in games:
        gid = game["canonical_game_id"]
        # Allowlist, not denylist (defense-in-depth at this package's
        # own boundary, mirroring the adapter in synthetic_bettors_v2.py):
        # only named raw facts survive, whatever else the caller's dict
        # happened to carry.
        facts = build_raw_game_facts(game)
        candidate_games.append((gid, facts))
        game_by_id[gid] = facts
        offered_lines[gid] = construct_offered_line(conn, gid, as_of)
        offered_totals[gid] = construct_offered_total(conn, gid, as_of)

    all_wagers = []
    wagers_by_game = {gid: [] for gid in game_by_id}
    total_wagers_by_game = {gid: [] for gid in game_by_id}
    active_bettors = 0
    total_wagers = 0
    total_handle = 0.0

    for profile in profiles:
        rng = random.Random(
            wager_rng_seed(simulation_run_key, population_version, profile["bettor_id"])
        )

        wager_count = select_wager_count(rng, profile)
        ordinal = 0  # scoped to ACCEPTED wagers only -- a skipped attempt never consumes one

        for _ in range(wager_count):
            gid = select_game(rng, profile, candidate_games)
            if gid is None:
                continue

            line = offered_lines.get(gid)
            if not line or line.get("favorite_side") not in ("home", "away"):
                continue  # no legitimate offered line -> skip, never invent one

            side, _p = select_spread_side(rng, profile, game_by_id[gid], line)
            if side is None:
                continue

            price_key = "home_price" if side == "home" else "away_price"
            price = line.get(price_key)
            if price is None:
                continue  # no legitimate price for the SELECTED side -> skip, never fabricate one

            stake = select_stake(rng, profile)
            # Signed line attached to the SIDE ACTUALLY TAKEN: a home
            # bettor gets home_spread as-is (e.g. -3.5); an away bettor
            # gets its mirror (+3.5) -- never home_spread unchanged for
            # both sides.
            accepted_line = line["home_spread"] if side == "home" else -line["home_spread"]

            wager = {
                "bettor_id": profile["bettor_id"],
                "population_version": population_version,
                # str(): synthetic_wager.canonical_game_id is TEXT (per
                # SCHEMA_SQL); SQLite's TEXT column affinity silently
                # coerces an inserted int to its text representation on
                # write, so a caller whose canonical_game_id is an int
                # (e.g. games_canonical's INTEGER PRIMARY KEY) would
                # otherwise see the loud post-insert consistency check
                # in _persist_wagers spuriously fail every single time
                # (int 1 != the '1' SQLite actually stored). Normalizing
                # here keeps the in-memory wager dict's value identical
                # to what the database will hand back.
                "canonical_game_id": str(gid),
                "sport": sport,
                "market": "spread",
                "side": side,
                "stake": stake,
                "accepted_line": accepted_line,
                "accepted_price": price,
                "reference_books_used": ",".join(line.get("reference_books_used") or []),
                # Both set to as_of: Phase 2A introduces no separate
                # booking-delay concept, and a wall-clock accepted_at
                # (datetime.now()) would reintroduce exactly the
                # nondeterminism run_identity.py exists to remove. A
                # later phase that models staggered acceptance times
                # would derive accepted_at from the run's own inputs,
                # never from process wall-clock time.
                "as_of_decision": as_of,
                "accepted_at": as_of,
                "simulation_run_id": simulation_run_id,
                "wager_ordinal": ordinal,
            }
            ordinal += 1

            all_wagers.append(wager)
            wagers_by_game[gid].append(wager)
            total_wagers += 1
            total_handle += stake

        if ordinal > 0:
            active_bettors += 1

    # Phase 2B totals: same persistent bettors and simulation run, but an
    # independent RNG namespace so totals can never perturb spread wagers.
    for profile in profiles:
        rng = random.Random(
            total_wager_rng_seed(simulation_run_key, population_version, profile["bettor_id"])
        )

        wager_count = select_wager_count(rng, profile)
        ordinal = TOTAL_WAGER_ORDINAL_BASE

        for _ in range(wager_count):
            gid = select_game(rng, profile, candidate_games)
            if gid is None:
                continue

            offered_total = offered_totals.get(gid)
            if not offered_total or offered_total.get("total_points") is None:
                continue

            side, _p = select_total_side(
                rng, profile, game_by_id[gid], offered_total
            )
            if side not in ("over", "under"):
                continue

            price_key = "over_price" if side == "over" else "under_price"
            price = offered_total.get(price_key)
            if price is None:
                continue

            stake = select_stake(rng, profile)
            wager = {
                "bettor_id": profile["bettor_id"],
                "population_version": population_version,
                "canonical_game_id": str(gid),
                "sport": sport,
                "market": "total",
                "side": side,
                "stake": stake,
                "accepted_line": offered_total["total_points"],
                "accepted_price": price,
                "reference_books_used": ",".join(
                    offered_total.get("reference_books_used") or []
                ),
                "as_of_decision": as_of,
                "accepted_at": as_of,
                "simulation_run_id": simulation_run_id,
                "wager_ordinal": ordinal,
            }
            ordinal += 1

            all_wagers.append(wager)
            total_wagers_by_game[gid].append(wager)
            total_wagers += 1
            total_handle += stake

    ledgers = {}
    for gid in game_by_id:
        line = offered_lines.get(gid) or {}
        game_ledger = build_game_ledger(gid, wagers_by_game[gid], line.get("home_spread"))
        game_ledger["favorite_side"] = line.get("favorite_side")
        game_ledger["underdog_side"] = (
            "away" if line.get("favorite_side") == "home"
            else "home" if line.get("favorite_side") == "away"
            else None
        )
        game_ledger["offered_home_spread"] = line.get("home_spread")
        offered_total = offered_totals.get(gid) or {}
        game_ledger["total_ledger"] = build_total_ledger(
            gid,
            total_wagers_by_game[gid],
            offered_total.get("total_points"),
        )
        ledgers[gid] = game_ledger

    active_bettors = len({w["bettor_id"] for w in all_wagers})

    if write_wagers and all_wagers:
        _persist_wagers(conn, all_wagers)

    return {
        "version": VERSION,
        "population_version": population_version,
        "sport": sport,
        "simulation_run_id": simulation_run_id,
        "simulation_run_key": simulation_run_key,
        "as_of": as_of,
        "population_size": len(profiles),
        "active_bettors": active_bettors,
        "total_wagers": total_wagers,
        "total_handle": round(total_handle, 2),
        "wagers": all_wagers,
        "games": ledgers,
    }
