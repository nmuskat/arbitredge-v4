"""
original_v3/totals.py -- TOTALS EXTENSION of the original ArbitrEdge Book NEED (NOT original: the V3 Book NEED was
spreads-only). Designed and frozen on 2026-11-12 BEFORE any totals result was computed.

Reuses the ORIGINAL code unchanged wherever it applies:
  segments (book_need.segments), staleness (is_stale), per-book market state (features.market_state_as_of, which
  already returns total_points / total_price_over / total_price_under), regime table (book_need.regime.classify_regime),
  juice orientation (book_need.juice.compute_J), cross-book vote (book_need.aggregation), segment selection,
  NEED decision table + strength modifiers (book_need.need_strength), Handle Intensity percentile
  (book_need.public_pressure.compute_public_pressure).

New pieces (the only invented parts, fixed a priori):
  * Public totals side:
      variant A "over_bias": always OVER (public over bias; direction always clear).
      variant B "scoring_lean": sign of the two teams' recent combined scoring (points for + against, average of
        the last 3 games, from compute_features_v2) minus the slate mean; clear only when |lean| is above the
        slate's 50th percentile (the original's PRESSURE_DIRECTION_MEDIAN_PERCENTILE rule).
  * S for totals: +1 when the total moved toward the pressured side (up for over, down for under), -1 against,
    0 held -- the spread S definition transposed.
  * Key numbers: the original's key numbers are spread numbers, so no totals key-number state exists; the
    original's RESISTANCE "no key-number corroboration" downgrade therefore always applies (disclosed).
NEED = the side opposite the pressured side, strength from the original table.
"""
from book_need.aggregation import aggregate_book_regimes, check_prior_contradiction, select_current_segment
from book_need.juice import compute_J
from book_need.need_strength import apply_modifiers_and_cap
from book_need.public_pressure import _find_live_slate_games, compute_public_pressure
from book_need.regime import classify_regime
from book_need.segments import NOT_YET_OBSERVED, is_stale, resolve_segments
from features.build_engineered_features_v2 import compute_features_v2
from features.market_state_as_of import PostKickoffCutoffError, market_state_as_of, resolve_named_cutoff
from reporting.checkpoint_reconstruction import get_books_for_game

VARIANTS = ("over_bias", "scoring_lean")
OPP = {"over": "under", "under": "over"}


def _combined_recent(conn, gid, cutoff):
    try:
        f = compute_features_v2(conn, gid, cutoff)
    except PostKickoffCutoffError:
        return None
    vals = [f.get(f"{s}_points_{k}_avg_last_3") for s in ("home", "away") for k in ("for", "against")]
    if any(v is None for v in vals):
        return None
    return sum(vals) / 2.0


def scoring_lean(conn, gid, sport, commence, named_cutoff):
    """(direction or None, clear?) -- variant B."""
    tgt = _combined_recent(conn, gid, resolve_named_cutoff(commence, named_cutoff))
    if tgt is None:
        return None, False
    slate = []
    for cg, cc in _find_live_slate_games(conn, gid, sport, commence):
        v = _combined_recent(conn, cg, resolve_named_cutoff(cc, named_cutoff))
        if v is not None:
            slate.append(v)
    if len(slate) < 2:
        return None, False
    mean = (sum(slate) + tgt) / (len(slate) + 1)
    lean = tgt - mean
    mags = sorted(abs(v - mean) for v in slate)
    pct = 100.0 * sum(1 for m in mags if m < abs(lean)) / len(mags)
    if lean == 0:
        return None, False
    return ("over" if lean > 0 else "under"), pct > 50


def _S(pressure, start, end):
    if start is None or end is None:
        return None
    d = (end - start) * (1 if pressure == "over" else -1)
    return 1 if d > 0 else (-1 if d < 0 else 0)


def compute_totals_inference(conn, gid, as_of, variant):
    sport, home, away, commence = conn.execute(
        "SELECT sport, home_team_id, away_team_id, commence_time FROM games_canonical WHERE canonical_game_id=?",
        (gid,)).fetchone()
    books = get_books_for_game(conn, gid)
    segs = resolve_segments(commence, as_of)
    for s in segs:
        s.update(aggregate_regime=None, breadth=None, per_book={}, public_pressure=None, pressure=None, clear=False)
        if s["status"] == NOT_YET_OBSERVED:
            continue
        pp = compute_public_pressure(conn, gid, sport, commence, s["start_label"], hi_as_of=s["evaluation_horizon"])
        s["public_pressure"] = pp
        if pp is None:
            continue
        if variant == "over_bias":
            s["pressure"], s["clear"] = "over", True
        else:
            s["pressure"], s["clear"] = scoring_lean(conn, gid, sport, commence, s["start_label"])
        p = s["pressure"] or "over"
        regimes = []
        for bk in books:
            try:
                a = market_state_as_of(conn, gid, s["start"], bk)
                b = market_state_as_of(conn, gid, s["evaluation_horizon"], bk)
            except PostKickoffCutoffError:
                s["per_book"][bk] = {"regime": "INSUFFICIENT"}
                continue
            if a is None or b is None or a["total_points"] is None or b["total_points"] is None:
                s["per_book"][bk] = {"regime": "INSUFFICIENT"}
                continue
            if is_stale(commence, s["evaluation_horizon"], b["market_observation_timestamp"]):
                s["per_book"][bk] = {"regime": "INSUFFICIENT", "reason": "stale"}
                continue
            pk = "total_price_over" if p == "over" else "total_price_under"
            S, J = _S(p, a["total_points"], b["total_points"]), compute_J(a[pk], b[pk])
            reg = classify_regime(S, J)
            s["per_book"][bk] = {"regime": reg, "S": S, "J": J}
            regimes.append(reg)
        s["aggregate_regime"], s["breadth"] = aggregate_book_regimes(regimes)
    cur, fallback = select_current_segment(segs)
    out = {"variant": variant, "segment": cur["name"] if cur else None, "side": None, "strength": None,
           "regime": cur["aggregate_regime"] if cur else None}
    if cur is None or cur["public_pressure"] is None:
        out["side"] = "INSUFFICIENT_EVIDENCE"
        return out
    usable = sum(1 for r in cur["per_book"].values() if r["regime"] != "INSUFFICIENT")
    if usable < 2:
        out["side"] = "INSUFFICIENT_EVIDENCE"
        return out
    if not cur["clear"] or cur["pressure"] is None or cur["aggregate_regime"] in (None, "MIXED"):
        out["side"] = "NO_CLEAR_NEED"
        return out
    base = {"RESISTANCE": "STRONG", "DIVERGENCE": "STRONG", "PARTIAL_DIVERGENCE": "MODERATE",
            "CONFIRMATION": "LEAN"}.get(cur["aggregate_regime"])
    if base is None:
        out["side"] = "NO_CLEAR_NEED"
        return out
    out["side"] = OPP[cur["pressure"]]
    out["strength"] = apply_modifiers_and_cap(base, cur["breadth"], check_prior_contradiction(cur, segs),
                                              cur["aggregate_regime"], [], cur["public_pressure"])
    out["pressure"] = cur["pressure"]
    return out
