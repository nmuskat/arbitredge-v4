"""
simulation/run_identity.py

Phase 2A. Separates two concepts that Phase 1 conflated inside
population._wager_seed():

  1. as_of -- the MARKET OBSERVATION CUTOFF. Controls which per-book
     market_snapshots rows are visible (features.market_state_as_of)
     and therefore what offered line/price a bettor sees. This can
     legitimately be "now" at call time, and re-running the same
     logical slate minutes apart with a slightly later as_of is
     normal, expected production behavior -- the market may genuinely
     have moved.

  2. simulation_run_key -- a STABLE identity for "this slate's wager-
     generation run," explicit, caller-supplied, independent of the
     wall-clock value of as_of. This is what seeds each bettor's RNG
     and what the deterministic per-wager ordinal is scoped to. Two
     invocations of the same logical run (same sport, same betting
     day, same population_version) must draw the SAME sequence of
     bettor decisions regardless of how many seconds apart as_of
     happens to land -- otherwise every re-run of the orchestration
     would mint a structurally new set of "distinct" wagers, defeating
     idempotent persistence.

Neither value is ever datetime.now() or uuid4() -- both are plain
strings the caller controls, and simulation_run_id (below) is a pure
SHA-256 function of documented inputs only.
"""

import hashlib

# v2 (R2): the hash inputs changed (as_of removed -- see
# compute_simulation_run_id's docstring), so the version bumps too.
# Nothing in production ever persisted a v1 ID (Phase 1/R1 never
# wrote to synthetic_wager), so there is no migration concern here --
# this just keeps the version string honest about what it hashes.
RUN_ID_VERSION = "simulation_run_id_v2"


def default_population_version(sport):
    """
    The single source of truth for V3's default population_version
    string, shared by book_need/synthetic_bettors_v2.py (which builds
    the V3 shadow population) and book_need/run_slate_book_need.py
    (which must compute the SAME population_version to build a
    matching simulation_run_key) -- previously this was a literal
    f"{sport}_2026_v1" inlined only inside synthetic_bettors_v2.py,
    with nothing stopping a second inlined copy from drifting out of
    sync.
    """
    return f"{sport}_2026_v1"


def build_simulation_run_key(sport, slate_date, population_version):
    """
    The stable identity most callers want: one run per
    (sport, betting-day, population_version), deliberately NOT
    including as_of. book_need/run_slate_book_need.py uses this so
    that re-invoking the orchestration later the same betting day (as
    production does -- it has no fixed schedule) reproduces the same
    bettor decisions rather than drawing a fresh, unrelated set every
    time as_of ticks forward.

    slate_date: a date string (e.g. "2026-10-05"), already how
    run_slate_book_need.py groups games into daily pools -- not a
    full timestamp.
    """
    return f"{sport}|{slate_date}|{population_version}"


def compute_simulation_run_id(population_version, sport, simulation_run_key, bettor_count):
    """
    Deterministic SHA-256 identifier for the LOGICAL sportsbook run --
    NOT for one observation of it.

    R2 correction: R1 included as_of in this hash. That was wrong: it
    meant the same logical run (same simulation_run_key, same
    population, same bettor_count), re-inspected later the same day
    purely because as_of ticked forward on a dashboard refresh, got a
    brand-new simulation_run_id. Since synthetic_wager's idempotency
    and append-only guarantees are SCOPED to simulation_run_id (see
    population._persist_wagers), that would have let the same logical
    run accept a second complete set of "new" wagers merely because
    the market cutoff advanced -- exactly the duplicate-acceptance bug
    the append-only design exists to prevent.

    Documented inputs, nothing else -- all identity-defining MODEL
    inputs, never the market-observation cutoff:
      - population_version: which trait calibration generated these
        bettors
      - sport: "nfl" | "cfb"
      - simulation_run_key: the stable per-slate identity (see above)
      - bettor_count: population size this run was generated against

    as_of is deliberately NOT here. It is still fully preserved as
    provenance -- per-wager, via as_of_decision/accepted_line/
    accepted_price/reference_books_used on each persisted row -- just
    not folded into the identity that gates whether a wager is "the
    same wager" or "a new one". Two calls with the same
    simulation_run_key/population_version/bettor_count but different
    as_of now produce the SAME simulation_run_id by design: they are
    the same logical run, inspected (or replayed) at a later market
    cutoff, not two different runs.

    Never datetime.now(), never uuid4(), never any other source of
    wall-clock or process-local entropy.
    """
    raw = f"{RUN_ID_VERSION}|{population_version}|{sport}|{simulation_run_key}|{int(bettor_count)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def wager_rng_seed(simulation_run_key, population_version, bettor_id):
    """
    hashlib-based (never Python's hash()) per-bettor RNG seed, scoped
    to simulation_run_key rather than as_of -- see module docstring.
    """
    raw = f"{simulation_run_key}|{population_version}|{bettor_id}|wager_selection_v2"
    return int(hashlib.sha256(raw.encode()).hexdigest()[:16], 16)

def total_wager_rng_seed(simulation_run_key, population_version, bettor_id):
    """
    Deterministic per-bettor RNG seed for Phase 2B totals selection.

    Uses the same persistent bettor identity and logical simulation run
    as spreads, but a separate RNG namespace so totals decisions can
    never consume or perturb the existing spread wager RNG sequence.
    """
    raw = f"{simulation_run_key}|{population_version}|{bettor_id}|total_wager_selection_v1"
    return int(hashlib.sha256(raw.encode()).hexdigest()[:16], 16)
