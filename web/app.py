"""
web/app.py

ArbitrEdge Web Dashboard -- Phase 1, LAN-only, read-only.

This is a presentation/orchestration layer ONLY. It imports and calls the
existing, locked book_need engine directly -- no model logic, no
threshold, no regime rule, no Need Strength rule is duplicated or
reinterpreted here. Every value the frontend displays was already
computed by book_need before this file touches it. The only "logic" in
this file is: (1) a short-lived cache to avoid recomputing an expensive
full-slate calculation on every browser auto-refresh, (2) JSON shaping/
formatting of already-computed fields (team display names, Central Time
conversion, a plain-English "Why" sentence assembled from fields that
were already decided elsewhere), and (3) error surfacing.

persist is ALWAYS False here -- normal dashboard usage, including
auto-refresh, creates zero book_need_prospective_records. Prospective
persistence remains a separate, not-yet-approved workflow.
"""

import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, request, render_template

from db.migrate import DEFAULT_DB_PATH
from db.connection import get_connection
from book_need.run_slate_book_need import run_book_need_slate, _cfb_v3_top25_eligible
from features.market_freshness import game_freshness, sport_freshness, STALE, NO_DATA

app = Flask(__name__, static_folder="static", template_folder="templates")

CENTRAL = ZoneInfo("America/Chicago")
UTC = timezone.utc

CACHE_TTL_SECONDS = 300
DISCOVERY_WINDOW_DAYS = 35  # "upcoming" games from now through roughly the next full slate cycle -- widened from 9 after confirming real Week 1 games sit ~27 days out from a typical current date, which a 9-day window could never reach

_cache_lock = threading.Lock()
_cache = {}  # key: (sport, minute_bucket) -> {"data": ..., "computed_at": ..., "as_of": ...}
_refreshing = set()

DIRECTIONAL_STRENGTHS = {"STRONG", "MODERATE", "LEAN"}
NON_DIRECTIONAL_STATES = {"NO_CLEAR_NEED", "INSUFFICIENT_EVIDENCE", "NOT_YET_EVALUABLE"}


def _get_conn():
    return get_connection(DEFAULT_DB_PATH)


def _team_display_names(conn):
    rows = conn.execute("SELECT canonical_team_id, display_name FROM teams_canonical").fetchall()
    return {cid: dn for cid, dn in rows}


def _to_central(iso_ts):
    if iso_ts is None:
        return None
    ts = iso_ts.replace("Z", "+00:00")
    dt_utc = datetime.fromisoformat(ts)
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    dt_central = dt_utc.astimezone(CENTRAL)
    return dt_central.strftime("%a %b %d, %I:%M %p %Z")


def _team_name(display_names, team_id):
    return display_names.get(team_id, team_id)


def _card_state(result):
    """The single filterable/badge state, derived purely from already-
    computed fields -- never a new decision, just a selection of which
    already-decided value to surface as the headline state."""
    side = result["inferred_book_need_side"]
    if side in NON_DIRECTIONAL_STATES:
        return side
    return result["need_strength"]  # STRONG / MODERATE / LEAN


def _build_why(result, display_names):
    """Plain-English sentence assembled ENTIRELY from fields book_need
    already computed and decided -- no new inference happens here."""
    side = result["inferred_book_need_side"]
    if side == "NOT_YET_EVALUABLE":
        return "The T-24h evaluation window has not opened yet for this game."
    if side == "INSUFFICIENT_EVIDENCE":
        return "The evaluation window is open, but usable market/behavioral data is currently inadequate."
    if side == "NO_CLEAR_NEED":
        return "Adequate data exists, but the signals do not converge on a clear directional need."

    pp = result["public_pressure"] or {}
    direction = pp.get("side_preference_direction")
    pressure_team = _team_name(display_names, result["home_team_id"] if direction == "home" else result["away_team_id"])
    need_team = _team_name(display_names, result["home_team_id"] if side == "home" else result["away_team_id"])
    seg_name = result["current_segment_name"]
    current_seg = next((s for s in result["segments"] if s["name"] == seg_name), None)
    regime = current_seg.get("aggregate_regime") if current_seg else None

    return (f"Expected recreational attention leans {pressure_team}; the market's {regime} "
            f"response (segment {seg_name}) suggests the book may still need {need_team}.")


def _shape_result(result, display_names, freshness=None):
    pp = result["public_pressure"]
    seg_name = result["current_segment_name"]
    current_seg = next((s for s in result["segments"] if s["name"] == seg_name), None) if seg_name else None

    side = result["inferred_book_need_side"]
    inferred_team = None
    if side in ("home", "away"):
        inferred_team = _team_name(display_names, result["home_team_id"] if side == "home" else result["away_team_id"])

    sp_team = None
    if pp and pp["side_preference_direction"] in ("home", "away"):
        sp_team = _team_name(display_names, result["home_team_id"] if pp["side_preference_direction"] == "home" else result["away_team_id"])

    segments_out = []
    for seg in result["segments"]:
        segments_out.append({
            "name": seg["name"], "status": seg["status"],
            "aggregate_regime": seg.get("aggregate_regime"), "breadth": seg.get("breadth"),
            "per_book": {
                book: {"regime": r.get("regime"), "S": r.get("S"), "J": r.get("J"),
                       "key_number_state": r.get("key_number_state"),
                       "contradiction": r.get("contradiction"),
                       "intra_segment_reversal": r.get("intra_segment_reversal"),
                       "reason": r.get("reason")}
                for book, r in seg.get("per_book", {}).items()
            },
        })

    return {
        "canonical_game_id": result["canonical_game_id"],
        "sport": result["sport"],
        "away_team": _team_name(display_names, result["away_team_id"]),
        "home_team": _team_name(display_names, result["home_team_id"]),
        "kickoff_central": _to_central(result["commence_time"]),
        "as_of": result["as_of"],
        "card_state": _card_state(result),
        "inferred_book_need_side": side,
        "inferred_book_need_team": inferred_team,
        "signal_spread": result.get("signal_line", {}).get("spread") if result.get("signal_line") else None,
        "signal_line_book_count": result.get("signal_line", {}).get("book_count") if result.get("signal_line") else None,
        "signal_line_as_of": result.get("signal_line", {}).get("as_of") if result.get("signal_line") else None,
        "need_strength": result["need_strength"],
        "data_quality": result["data_quality"],
        "handle_intensity_percentile": pp["handle_intensity_percentile"] if pp else None,
        "side_preference_team": sp_team,
        "side_preference_direction": pp["side_preference_direction"] if pp else None,
        "side_preference_magnitude_percentile": pp["side_preference_magnitude_percentile"] if pp else None,
        "side_preference_magnitude_undefined": pp["side_preference_magnitude_undefined"] if pp else None,
        "current_regime": current_seg.get("aggregate_regime") if current_seg else None,
        "governing_segment": seg_name,
        "why": _build_why(result, display_names),
        "earliest_evaluation_window_start_central": _to_central(result.get("earliest_evaluation_window_start")),
        "contradiction_flag": result["contradiction_flag"],
        "fallback_occurred": result["fallback_occurred"],
        "synthetic_sportsbook": result.get("synthetic_sportsbook") if result.get("sport") in ("nfl", "cfb") else None,
        "synthetic_nfl_slate": result.get("synthetic_nfl_slate") if result.get("sport") == "nfl" else None,
        "synthetic_v3_totals": (
            (
                (
                    result.get("synthetic_nfl_slate_v3_shadow")
                    or result.get("synthetic_cfb_slate_v3_shadow")
                    or {}
                ).get("game_ledger") or {}
            ).get("total_ledger")
        ),
        "segments": segments_out,
        # Explicit data age: every value above was computed from market data
        # last observed at freshness["last_market_observation"]. STALE/NO_DATA
        # means collection is not keeping its own cadence for this game.
        "market_freshness": freshness,
        "market_data_stale": bool(freshness and freshness["status"] in (STALE, NO_DATA)),
    }


def _shape_excluded(excluded, display_names):
    return [{
        "canonical_game_id": e["canonical_game_id"],
        "away_team": _team_name(display_names, e["away_team_id"]),
        "home_team": _team_name(display_names, e["home_team_id"]),
        "status": e["status"],
        "reason": e["reason"],
    } for e in excluded]


def _purge_expired_cache_entries():
    """Deterministic eviction: removes any cache entry whose TTL has
    already expired. Called on every cache access so the dict never
    accumulates one stale entry per sport per minute-bucket for the life
    of a long-running process. No external cache, no new dependency."""
    now = time.time()
    expired_keys = [k for k, v in _cache.items() if (now - v["computed_at"]) >= CACHE_TTL_SECONDS]
    for k in expired_keys:
        del _cache[k]


def _compute_slate(sport):
    """Compute a fresh slate and replace the cache only after success."""
    now = datetime.now(UTC)
    cache_key = sport
    as_of = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    start_date = now.strftime("%Y-%m-%d")
    end_date = (now + timedelta(days=DISCOVERY_WINDOW_DAYS)).strftime("%Y-%m-%d")

    conn = _get_conn()
    try:
        display_names = _team_display_names(conn)
        sports = ["nfl", "cfb"] if sport == "all" else [sport]
        all_results, all_excluded = [], []
        sport_cache_data = {}
        freshness_by_gid = {}
        collection_freshness = sport_freshness(conn, now)

        for s in sports:
            slate = run_book_need_slate(
                conn, s, start_date, end_date, as_of=as_of, persist=False
            )

            if s == "cfb":
                slate["results"] = [r for r in slate["results"] if _cfb_v3_top25_eligible(conn, r["canonical_game_id"], as_of)]

            for r in slate["results"]:
                freshness_by_gid[r["canonical_game_id"]] = game_freshness(conn, r["canonical_game_id"], now)

            if sport == "all":
                sport_cache_data[s] = {
                    "as_of": as_of,
                    "games": [_shape_result(r, display_names, freshness_by_gid.get(r["canonical_game_id"]))
                              for r in slate["results"]],
                    "excluded": _shape_excluded(slate["excluded"], display_names),
                    "market_freshness": collection_freshness,
                }

            all_results.extend(slate["results"])
            all_excluded.extend(slate["excluded"])

        data = {
            "as_of": as_of,
            "games": [_shape_result(r, display_names, freshness_by_gid.get(r["canonical_game_id"]))
                      for r in all_results],
            "excluded": _shape_excluded(all_excluded, display_names),
            "market_freshness": collection_freshness,
        }
    finally:
        conn.close()

    computed_at = time.time()

    with _cache_lock:
        _cache[cache_key] = {"data": data, "computed_at": computed_at}

        if sport == "all":
            for cached_sport, cached_data in sport_cache_data.items():
                _cache[cached_sport] = {
                    "data": cached_data,
                    "computed_at": computed_at,
                }

        _refreshing.discard(sport)

    return data, computed_at


def _background_refresh(sport):
    try:
        _compute_slate(sport)
    except Exception:
        app.logger.exception("Background slate refresh failed for %s", sport)
        with _cache_lock:
            _refreshing.discard(sport)


def _get_slate(sport):
    """Serve the last successful slate while an expired cache refreshes."""
    with _cache_lock:
        _purge_expired_cache_entries()

    cache_key = sport
    launch_refresh = False

    with _cache_lock:
        cached = _cache.get(cache_key)

        if cached:
            age = time.time() - cached["computed_at"]

            if age < CACHE_TTL_SECONDS:
                return cached["data"], cached["computed_at"]

            if sport not in _refreshing:
                _refreshing.add(sport)
                launch_refresh = True

            stale_data = cached["data"]
            stale_computed_at = cached["computed_at"]
            del _cache[cache_key]
        else:
            stale_data = None
            stale_computed_at = None

    if stale_data is not None:
        if launch_refresh:
            threading.Thread(
                target=_background_refresh,
                args=(sport,),
                daemon=True,
            ).start()

        return stale_data, stale_computed_at

    return _compute_slate(sport)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/slate")
def api_slate():
    sport = request.args.get("sport", "all").lower()
    if sport not in ("nfl", "cfb", "all"):
        return jsonify({"error": f"invalid sport {sport!r}, expected nfl/cfb/all"}), 400

    try:
        data, computed_at = _get_slate(sport)
    except Exception as e:
        return jsonify({"error": f"backend error computing slate: {e}"}), 500

    games = data["games"]
    excluded = data["excluded"]
    counts = {
        "upcoming_games": len(games) + len(excluded),
        "currently_evaluable": sum(1 for g in games if g["card_state"] != "NOT_YET_EVALUABLE"),
        "not_yet_evaluable": sum(1 for g in games if g["card_state"] == "NOT_YET_EVALUABLE"),
        "insufficient_evidence": sum(1 for g in games if g["card_state"] == "INSUFFICIENT_EVIDENCE"),
        "no_clear_need": sum(1 for g in games if g["card_state"] == "NO_CLEAR_NEED"),
        "directional_book_needs": sum(1 for g in games if g["card_state"] in DIRECTIONAL_STRENGTHS),
        "excluded": len(excluded),
    }

    stale_games = sum(1 for g in games if g.get("market_data_stale"))
    return jsonify({
        "market_freshness": data.get("market_freshness"),
        "stale_market_games": stale_games,
        "as_of": data["as_of"],
        "as_of_central": _to_central(data["as_of"]),
        "data_computed_at_unix": computed_at,
        "cache_age_seconds": round(time.time() - computed_at, 1),
        "counts": counts,
        "games": games,
        "excluded": excluded,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
