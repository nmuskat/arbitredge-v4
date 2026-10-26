"""
book_need/run_slate_book_need.py

Ties RUN SLATE discovery + eligibility to the Book Need inference engine.
persist defaults to False -- prospective writes only happen when
explicitly requested, never automatically.

NFL V2 uses one shared synthetic bettor population across the eligible slate.
"""

import sys
import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_slate.discover_slate import discover_upcoming_slate, _now_str
from features.run_slate_eligibility import check_run_slate_eligibility, ELIGIBLE
from book_need.inference import compute_inference
from book_need.signal_line import get_representative_signal_line
from book_need.prospective_record import write_prospective_record
from book_need.synthetic_bettors_v2 import (
    simulate_shared_nfl_population,
    run_shadow_v3_population,
)
from simulation.run_identity import build_simulation_run_key, default_population_version


CENTRAL_TZ = ZoneInfo("America/Chicago")


def _v3_live_enabled():
    """Explicit kill-switch for promoting V3 from shadow to live decisions."""
    return os.environ.get("V3_BOOK_NEED_LIVE", "0").strip().lower() in {"1", "true", "yes", "on"}


def _promote_v3_live(conn, inference, v3_ledger):
    """Promote a valid V3 monetary Need when it converges with the market inference."""
    if not _v3_live_enabled() or not v3_ledger:
        return inference

    v3_side = v3_ledger.get("ledger_implied_need_side")
    legacy_side = inference.get("inferred_book_need_side")
    valid_direction = v3_side in ("home", "away")
    market_directional = legacy_side in ("home", "away")

    sportsbook = {
        "version": "synthetic_sportsbook_v3_monetary",
        "book_need_side": v3_side,
        "decision": "PASS",
        "total_handle": v3_ledger.get("total_handle"),
        "pnl_if_home_covers": v3_ledger.get("pnl_if_home_covers"),
        "pnl_if_away_covers": v3_ledger.get("pnl_if_away_covers"),
        "pnl_swing": v3_ledger.get("pnl_swing"),
        "ledger_implied_need_magnitude": v3_ledger.get("ledger_implied_need_magnitude"),
        "synthetic_bettor_ledger": v3_ledger,
    }

    if not valid_direction or not market_directional or v3_side != legacy_side:
        inference["synthetic_sportsbook"] = sportsbook
        inference["inferred_book_need_side"] = "NO_CLEAR_NEED"
        inference["need_strength"] = None
        inference["signal_line"] = None
        return inference

    sportsbook["decision"] = "BOOK_NEED"
    inference["synthetic_sportsbook"] = sportsbook
    inference["inferred_book_need_side"] = v3_side
    inference["signal_line"] = get_representative_signal_line(
        conn,
        inference["canonical_game_id"],
        inference["as_of"],
        v3_side,
        commence_time=inference.get("commence_time"),
    )
    return inference


def _betting_day(commence_time):
    """Return the U.S. Central calendar date for a UTC game kickoff."""
    value = str(commence_time)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    kickoff = datetime.fromisoformat(value)
    if kickoff.tzinfo is None:
        raise ValueError(f"commence_time must be timezone-aware: {commence_time!r}")
    return kickoff.astimezone(CENTRAL_TZ).date().isoformat()


def _cfb_v3_top25_eligible(conn, canonical_game_id, as_of):
    """Return True when latest point-in-time context across a reconciled game identity has an AP Top-25 team."""
    row = conn.execute(
        """
        SELECT gc.ranking_home, gc.ranking_away
        FROM game_context AS gc
        WHERE (
            gc.canonical_game_id = ?
            OR gc.canonical_game_id IN (
                SELECT duplicate_game_id
                FROM game_canonical_redirects
                WHERE survivor_game_id = ?
            )
        )
          AND gc.observed_at <= ?
        ORDER BY gc.observed_at DESC, gc.context_id DESC
        LIMIT 1
        """,
        (canonical_game_id, canonical_game_id, as_of),
    ).fetchone()

    if row is None:
        return False

    return any(
        isinstance(rank, int) and 1 <= rank <= 25
        for rank in (row[0], row[1])
    )


def _latest_v2_context(conn, canonical_game_id, as_of):
    """
    Raw, point-in-time engineered_features_v2 columns for one game.
    Feeds BOTH the legacy ledger (simulate_shared_nfl_population, which
    only reads the fields it already recognized) and the V3 shadow
    adapter (run_shadow_v3_population -> build_raw_game_facts), which
    only reads what's in ALLOWED_RAW_GAME_FACT_KEYS. Expanded (R3,
    correction #3) to also include the raw recency/rest columns V3's
    select_spread_side needs -- competing_games/standalone/tv_network/
    rankings alone meant its relative-recency and relative-rest terms
    always collapsed to neutral in the real NFL shadow path, since
    those columns were never queried at all before this change.
    Deliberately still excludes every modeled/behavioral column
    (Side Preference, Handle Intensity, Public Pressure, market
    response, Book Need, split consensus, market anomaly) --
    engineered_features_v2 does not carry those anyway, but the
    SELECT list below is an explicit allowlist of raw columns, not
    "select *", specifically so it can't start carrying one by accident.
    """
    row = conn.execute(
        """
        SELECT
            competing_games_same_window_same_sport,
            is_standalone_same_sport,
            tv_network,
            ranking_home,
            ranking_away,
            home_wins_last_3,
            away_wins_last_3,
            home_point_margin_avg_last_3,
            away_point_margin_avg_last_3,
            home_current_win_streak,
            away_current_win_streak,
            home_days_since_last_game,
            away_days_since_last_game
        FROM engineered_features_v2
        WHERE canonical_game_id = ?
          AND point_in_time_cutoff <= ?
        ORDER BY point_in_time_cutoff DESC, feature_id DESC
        LIMIT 1
        """,
        (canonical_game_id, as_of),
    ).fetchone()

    if row is None:
        return {}

    return {
        "competing_games_same_window_same_sport": row[0],
        "is_standalone_same_sport": row[1],
        "tv_network": row[2],
        "ranking_home": row[3],
        "ranking_away": row[4],
        "home_wins_last_3": row[5],
        "away_wins_last_3": row[6],
        "home_point_margin_avg_last_3": row[7],
        "away_point_margin_avg_last_3": row[8],
        "home_current_win_streak": row[9],
        "away_current_win_streak": row[10],
        "home_days_since_last_game": row[11],
        "away_days_since_last_game": row[12],
    }


def _shared_pool_game(conn, inference, as_of):
    pressure = inference.get("public_pressure") or {}
    game = {
        "canonical_game_id": inference["canonical_game_id"],
        "public_side": pressure.get("side_preference_direction"),
        "side_preference_percentile": pressure.get(
            "side_preference_magnitude_percentile"
        ),
        "market_activity_percentile": pressure.get(
            "handle_intensity_percentile"
        ),
    }
    game.update(_latest_v2_context(conn, inference["canonical_game_id"], as_of))
    return game


def run_book_need_slate(conn, sport, start_date, end_date, as_of=None, persist=False):
    as_of = as_of or _now_str()
    discovered = discover_upcoming_slate(conn, sport, start_date, end_date, as_of=as_of)

    eligible_games = []
    excluded = []

    for gid, home, away, commence_time in discovered:
        eligibility = check_run_slate_eligibility(conn, gid)
        if eligibility["status"] != ELIGIBLE:
            excluded.append({
                "canonical_game_id": gid,
                "home_team_id": home,
                "away_team_id": away,
                "status": eligibility["status"],
                "reason": eligibility["reason"],
            })
            continue
        eligible_games.append((gid, home, away, commence_time))

    results = []

    if sport.lower() == "nfl" and eligible_games:
        preliminary = []

        for gid, home, away, commence_time in eligible_games:
            inference = compute_inference(conn, gid, as_of)
            if inference is not None:
                preliminary.append(inference)

        # Build one shared 10,000-bettor population per NFL betting day.
        # The website may discover multiple days, but games on different
        # dates should not compete for the same synthetic ticket/handle pool.
        commence_by_gid = {
            gid: commence_time
            for gid, home, away, commence_time in eligible_games
        }
        daily_groups = {}
        for preliminary_inference in preliminary:
            gid = preliminary_inference["canonical_game_id"]
            game_date = _betting_day(commence_by_gid[gid])
            daily_groups.setdefault(game_date, []).append(preliminary_inference)

        shared_by_gid = {}
        shared_meta_by_gid = {}
        shadow_v3_by_gid = {}
        shadow_v3_meta_by_gid = {}

        for game_date, day_inferences in daily_groups.items():
            pool_games = [
                _shared_pool_game(conn, inference, as_of)
                for inference in day_inferences
            ]
            slate_key = f"nfl|{game_date}|{as_of}"

            # LEGACY -- unchanged call, unchanged signature, no conn/as_of.
            # This is the ONLY ledger that ever feeds compute_inference
            # below. There is no flag or parameter on this call that
            # could route it to the V3 engine.
            day_shared = simulate_shared_nfl_population(pool_games, slate_key)

            for day_inference in day_inferences:
                day_gid = day_inference["canonical_game_id"]
                shared_by_gid[day_gid] = day_shared["games"].get(day_gid)
                shared_meta_by_gid[day_gid] = day_shared

            # V3 persistent-population path. Returns None when
            # SYNTHETIC_BETTOR_ENGINE != "v3_persistent". Its monetary
            # ledger remains structurally separate from the legacy
            # synthetic_bettor_ledger and is NEVER passed into the
            # legacy synthetic sportsbook evaluator. When the explicit
            # V3_BOOK_NEED_LIVE kill-switch is enabled, _promote_v3_live
            # may promote the ledger-implied Need after market
            # convergence validation.
            #
            # simulation_run_key (Phase 2A) is explicit and deliberately
            # NOT as_of: it is stable for this (sport, betting day,
            # population_version) regardless of how many times this
            # orchestration re-runs that day with as_of ticking forward,
            # so replayed/re-run shadow calls reproduce the same bettor
            # decisions instead of minting a structurally "new" set on
            # every invocation. See simulation/run_identity.py.
            nfl_population_version = default_population_version("nfl")
            nfl_run_key = build_simulation_run_key("nfl", game_date, nfl_population_version)
            day_shadow_v3 = run_shadow_v3_population(
                pool_games, conn, as_of, sport="nfl", simulation_run_key=nfl_run_key, read_only=True
            )
            if day_shadow_v3 is not None:
                for day_inference in day_inferences:
                    day_gid = day_inference["canonical_game_id"]
                    shadow_v3_by_gid[day_gid] = day_shadow_v3["games"].get(day_gid)
                    shadow_v3_meta_by_gid[day_gid] = day_shadow_v3

        for preliminary_inference in preliminary:
            gid = preliminary_inference["canonical_game_id"]
            ledger = shared_by_gid.get(gid)
            shared = shared_meta_by_gid[gid]
            inference = compute_inference(
                conn,
                gid,
                as_of,
                synthetic_bettor_ledger=ledger,
            )
            if inference is None:
                continue

            inference = _promote_v3_live(
                conn,
                inference,
                shadow_v3_by_gid.get(gid),
            )

            inference["synthetic_nfl_slate"] = {
                "version": shared["version"],
                "population_size": shared["population_size"],
                "active_bettors": shared["active_bettors"],
                "total_wagers": shared["total_wagers"],
                "total_handle": shared["total_handle"],
            }

            # Phase 1 shadow output ONLY -- inspection/storage, never fed
            # into compute_inference or read by book_need.synthetic_sportsbook.
            # Absent entirely (key not set) unless the V3 flag is on.
            if gid in shadow_v3_meta_by_gid:
                shadow_shared = shadow_v3_meta_by_gid[gid]
                inference["synthetic_nfl_slate_v3_shadow"] = {
                    "version": shadow_shared["version"],
                    "population_version": shadow_shared["population_version"],
                    "population_size": shadow_shared["population_size"],
                    "active_bettors": shadow_shared["active_bettors"],
                    "total_wagers": shadow_shared["total_wagers"],
                    "total_handle": shadow_shared["total_handle"],
                    "game_ledger": shadow_v3_by_gid.get(gid),
                }

            results.append(inference)

            if persist:
                write_prospective_record(conn, inference)
    else:
        # CFB V3: legacy market inference remains structurally separate from
        # the synthetic monetary ledger. AP Top-25 eligibility is applied
        # point-in-time before building the shared CFB V3 population.
        # Eligible V3 ledgers may be promoted to the live decision only when
        # V3_BOOK_NEED_LIVE is enabled and monetary Need converges with the
        # existing market inference. Canonical redirects are resolved by the
        # CFB V3 eligibility/context path without rewriting historical IDs.
        preliminary = []
        for gid, home, away, commence_time in eligible_games:
            inference = compute_inference(conn, gid, as_of)
            if inference is not None:
                preliminary.append((inference, commence_time))

        cfb_daily_groups = {}
        for inference, commence_time in preliminary:
            gid = inference["canonical_game_id"]
            if not _cfb_v3_top25_eligible(conn, gid, as_of):
                continue
            game_date = _betting_day(commence_time)
            cfb_daily_groups.setdefault(game_date, []).append(inference)

        # Only "cfb" is a V3-supported sport value here (matches
        # simulation.population.SCHEMA_SQL's sport CHECK constraint) --
        # this else branch is also reached for an NFL call with zero
        # eligible_games, and must not tag some other sport string's
        # shadow population as "cfb".
        sport_key = sport.lower()
        shadow_v3_by_gid = {}
        shadow_v3_meta_by_gid = {}
        if sport_key == "cfb":
            cfb_population_version = default_population_version("cfb")
            for game_date, day_inferences in cfb_daily_groups.items():
                pool_games = [
                    _shared_pool_game(conn, inference, as_of)
                    for inference in day_inferences
                ]
                cfb_run_key = build_simulation_run_key("cfb", game_date, cfb_population_version)
                day_shadow_v3 = run_shadow_v3_population(
                    pool_games, conn, as_of, sport="cfb", simulation_run_key=cfb_run_key, read_only=True
                )
                if day_shadow_v3 is not None:
                    for day_inference in day_inferences:
                        day_gid = day_inference["canonical_game_id"]
                        shadow_v3_by_gid[day_gid] = day_shadow_v3["games"].get(day_gid)
                        shadow_v3_meta_by_gid[day_gid] = day_shadow_v3

        for inference, _commence_time in preliminary:
            gid = inference["canonical_game_id"]

            # Promote the CFB V3 monetary ledger only when the explicit
            # V3 live kill-switch is enabled and its Need converges with
            # the existing market inference, matching the NFL V3 path.
            inference = _promote_v3_live(
                conn,
                inference,
                shadow_v3_by_gid.get(gid),
            )

            # Phase 2A V3 diagnostic output -- retained for inspection.
            # The monetary ledger remains structurally separate from the
            # legacy synthetic sportsbook evaluator.
            # Absent entirely (key not set) unless the V3 flag is on.
            if gid in shadow_v3_meta_by_gid:
                shadow_shared = shadow_v3_meta_by_gid[gid]
                inference["synthetic_cfb_slate_v3_shadow"] = {
                    "version": shadow_shared["version"],
                    "population_version": shadow_shared["population_version"],
                    "population_size": shadow_shared["population_size"],
                    "active_bettors": shadow_shared["active_bettors"],
                    "total_wagers": shadow_shared["total_wagers"],
                    "total_handle": shadow_shared["total_handle"],
                    "game_ledger": shadow_v3_by_gid.get(gid),
                }

            results.append(inference)

            if persist:
                write_prospective_record(conn, inference)

    return {
        "sport": sport,
        "as_of": as_of,
        "results": results,
        "excluded": excluded,
    }
