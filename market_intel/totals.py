"""
market_intel/totals.py (copied unchanged from research original_v3/totals2.py) -- TOTALS MARKET INTELLIGENCE ENGINE (extension of the original ArbitrEdge; NOT original).
Version "mi_totals.v1". Every rule below was FIXED and committed on 2026-11-12 BEFORE any result of this engine was
computed. (Disclosure: a simpler earlier totals extension, original_v3/totals.py, was already backtested and lost;
its "scoring lean" is one of the four components here.)

Reused UNCHANGED from the original: segments + staleness (book_need.segments), per-book market state
(features.market_state_as_of, which returns total_points / over / under prices), juice orientation
(book_need.juice.compute_J), regime table (book_need.regime.classify_regime), cross-book vote + segment selection +
contradiction (book_need.aggregation), NEED strength modifiers (book_need.need_strength.apply_modifiers_and_cap),
slate-relative z-scores / percentiles (behavioral._stats), slate definition (book_need.public_pressure: same sport,
kickoff within +-3 days), recency features (features.build_engineered_features_v2.compute_features_v2).

A. ESTIMATED PUBLIC TOTALS PREFERENCE (TPP) -- an estimate, never ticket/handle data. Positive = OVER.
   Four signed components, slate-z-scored, equal-weighted, coverage-normalized (the Side Preference V1 procedure):
     1. recent_scoring    combined recent game totals of the two teams: (home PF3 + home PA3 + away PF3 + away PA3)/2
     2. scoring_vs_line   recent_scoring - the current consensus total (narrative "value" on the over)
     3. over_streak       share of the two teams' last-3 completed games that went OVER their own consensus closing
                          total (each prior game's close known at its kickoff, result known 4 h later)
     4. attention         (Handle Intensity V1 percentile - 50) / 50: the documented public over-bias scaled by public
                          attention -- a context term, not a default (it is one of four equal terms and can be
                          outvoted; at median attention it is 0)
   Direction = sign of the normalized score. Pressure is CLEAR only if coverage >= 2 components and |score| is above
   the slate's 50th percentile (the original PRESSURE_DIRECTION_MEDIAN_PERCENTILE rule). Coverage 2-3 = labelled
   REDUCED INPUT; coverage < 2 = ABSTAIN.
B. TOTALS HANDLE INTENSITY (market activity, never money): Handle Intensity V2's five components computed on the
   TOTALS columns over the prior 6 h: total-line changes per active book, over/under juice changes per active book,
   share of books moving, last-hour acceleration, coordinated 10-min activity; slate z-scores, equal weight, slate
   percentile.
C. RESPONSE per book over the governing segment: S = total moved toward (+1) / against (-1) the pressured side,
   J = juice worse (+1) / better (-1) for the pressured side, original regime table, cross-book vote.
   Key totals (NFL only, deliberately short, in the spirit of the original's {3, 7}): {41, 44, 47}; states classified
   exactly as the original's key-number states. CFB: no key totals (no state).
   Professional vs recreational sequencing (DESCRIPTIVE ONLY, never used in the decision -- the original has no such
   rule): median move of Pinnacle/BetOnline/LowVig vs the other books over the segment.
D. TOTALS NEED: only with clear pressure, >= 2 usable books and a non-MIXED regime: NEED = the side OPPOSITE the
   estimated public side; base strength RESISTANCE/DIVERGENCE -> STRONG, PARTIAL_DIVERGENCE -> MODERATE,
   CONFIRMATION -> LEAN; original modifiers (majority-only, contradiction, resistance without key total) and the
   original cap (LEAN unless totals Handle Intensity > 50th pct). Otherwise PASS with the reason.
"""
import statistics as st

from behavioral._stats import slate_percentile_ranks, slate_zscore
from behavioral.handle_intensity_v1 import compute_handle_intensity_v1
from book_need.aggregation import aggregate_book_regimes, check_prior_contradiction, select_current_segment
from book_need.juice import compute_J
from book_need.need_strength import apply_modifiers_and_cap
from book_need.public_pressure import _find_live_slate_games
from book_need.regime import classify_regime
from book_need.segments import NOT_YET_OBSERVED, is_stale, resolve_segments
from features.build_engineered_features_v2 import compute_features_v2
from features.market_state_as_of import PostKickoffCutoffError, _parse_iso, market_state_as_of, resolve_named_cutoff
from reporting.checkpoint_reconstruction import get_books_for_game

VERSION = "mi_totals.v1"
KEY_TOTALS = {"nfl": (41, 44, 47), "cfb": ()}
PRO_BOOKS = ("pinnacle", "betonlineag", "lowvig")
OPP = {"over": "under", "under": "over"}
COMPONENTS = ("recent_scoring", "scoring_vs_line", "over_streak", "attention")
HI_COMPONENTS = ("line_change_rate", "juice_change_rate", "moving_book_share", "activity_acceleration",
                 "coordinated_activity")


# ------------------------------------------------------------------------------------------------ helpers
def _game(conn, gid):
    return conn.execute("SELECT sport, home_team_id, away_team_id, commence_time FROM games_canonical "
                        "WHERE canonical_game_id=?", (gid,)).fetchone()


def consensus_total(conn, gid, cutoff):
    """Median of each book's latest total at or before cutoff (effective time = book_last_update | observed_at)."""
    vals = []
    for (bk,) in conn.execute("SELECT DISTINCT book FROM market_snapshots WHERE canonical_game_id=?", (gid,)):
        try:
            s = market_state_as_of(conn, gid, cutoff, bk)
        except PostKickoffCutoffError:
            return None
        if s and s["total_points"] is not None:
            vals.append(s["total_points"])
    return st.median(vals) if vals else None


def _closing_total(conn, gid, commence):
    rows = conn.execute("SELECT book, MAX(observed_at), total_points FROM market_snapshots WHERE canonical_game_id=? "
                        "AND observed_at < ? AND total_points IS NOT NULL GROUP BY book", (gid, commence)).fetchall()
    vals = [r[2] for r in rows if r[2] is not None]
    return st.median(vals) if vals else None


def over_streak(conn, team, cutoff, cache):
    """(overs, games) over the team's last 3 games completed (result known) by cutoff."""
    rows = conn.execute(
        "SELECT g.canonical_game_id, g.commence_time, r.final_score_home, r.final_score_away FROM games_canonical g "
        "JOIN game_results r ON r.canonical_game_id=g.canonical_game_id WHERE (g.home_team_id=? OR g.away_team_id=?) "
        "AND g.commence_time < ? AND r.retrieved_at <= ? ORDER BY g.commence_time DESC LIMIT 3",
        (team, team, cutoff, cutoff)).fetchall()
    overs = n = 0
    for gid, ct, hs, as_ in rows:
        if gid not in cache:
            cache[gid] = _closing_total(conn, gid, ct)
        close = cache[gid]
        if close is None or hs is None or as_ is None or hs + as_ == close:
            continue
        n += 1
        overs += int(hs + as_ > close)
    return overs, n


def _raw_tpp(conn, gid, cutoff, cache):
    sport, home, away, commence = _game(conn, gid)
    try:
        f = compute_features_v2(conn, gid, cutoff)
    except PostKickoffCutoffError:
        return None, None
    parts = [f.get(f"{s}_points_{k}_avg_last_3") for s in ("home", "away") for k in ("for", "against")]
    rs = None if any(v is None for v in parts) else sum(parts) / 2.0
    ct = consensus_total(conn, gid, cutoff)
    o1, n1 = over_streak(conn, home, cutoff, cache)
    o2, n2 = over_streak(conn, away, cutoff, cache)
    raw = {"recent_scoring": rs, "scoring_vs_line": (rs - ct) if (rs is not None and ct is not None) else None,
           "over_streak": ((o1 + o2) / (n1 + n2)) if (n1 + n2) >= 2 else None}
    return raw, f


def _hi_raw(conn, gid, as_of):
    """Handle Intensity V2 components on the TOTALS columns, prior 6 h, point in time."""
    end = _parse_iso(as_of)
    from datetime import timedelta
    start, base, late = end - timedelta(hours=6), end - timedelta(hours=7), end - timedelta(hours=1)
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
    rows = conn.execute("SELECT book, observed_at, total_points, total_price_over FROM market_snapshots WHERE "
                        "canonical_game_id=? AND observed_at>=? AND observed_at<=? ORDER BY book, observed_at",
                        (gid, base.strftime(fmt), end.strftime(fmt))).fetchall()
    by = {}
    for bk, ob, tp, pr in rows:
        by.setdefault(bk, []).append((_parse_iso(ob), tp, pr))
    active, moving, lc, jc, late_ev, prior_ev, buckets = 0, set(), 0, 0, 0, 0, {}
    for bk, obs in by.items():
        if any(o[0] >= start for o in obs):
            active += 1
        prev = None
        for t, tp, pr in obs:
            if prev is None:
                prev = (tp, pr)
                continue
            lch = t >= start and tp is not None and prev[0] is not None and tp != prev[0]
            jch = t >= start and pr is not None and prev[1] is not None and pr != prev[1]
            lc += lch
            jc += jch
            if lch or jch:
                moving.add(bk)
                ev = int(lch) + int(jch)
                if t >= late:
                    late_ev += ev
                else:
                    prior_ev += ev
                buckets.setdefault(t.replace(minute=t.minute - t.minute % 10, second=0, microsecond=0), set()).add(bk)
            prev = (tp, pr)
    if not active:
        return None
    return {"line_change_rate": lc / active, "juice_change_rate": jc / active, "moving_book_share": len(moving) / active,
            "activity_acceleration": late_ev - prior_ev / 5.0,
            "coordinated_activity": sum(1 for b in buckets.values() if len(b) >= 2) / active}


def _key_state(sport, start, end):
    keys = KEY_TOTALS.get(sport) or ()
    if not keys or start is None or end is None:
        return None
    at_end = end in keys
    if start == end:
        return "AT_KEY_TOTAL" if at_end else "NO_KEY_TOTAL_INVOLVEMENT"
    lo, hi = sorted((start, end))
    if any(lo < k < hi for k in keys):
        return "CROSSED_KEY_TOTAL"
    d0, d1 = min(abs(start - k) for k in keys), min(abs(end - k) for k in keys)
    if d1 < d0 and at_end:
        return "MOVED_TOWARD_KEY_TOTAL_AND_STOPPED"
    if d1 < d0:
        return "MOVED_TOWARD_KEY_TOTAL_WITHOUT_REACHING"
    return "AT_KEY_TOTAL" if at_end else "NO_KEY_TOTAL_INVOLVEMENT"


# ------------------------------------------------------------------------------------------------ slate scores
def public_totals_pressure(conn, gid, named_cutoff, hi_as_of, cache):
    """TPP + totals HI for the target game, slate-relative (same slate rule as the original Public Pressure)."""
    sport, home, away, commence = _game(conn, gid)
    games = [(gid, commence)] + list(_find_live_slate_games(conn, gid, sport, commence))
    raws, feats, his = [], [], []
    for g, ct in games:
        cutoff = resolve_named_cutoff(ct, named_cutoff)
        r, f = _raw_tpp(conn, g, cutoff, cache)
        if r is None:
            if g == gid:
                return None
            continue
        raws.append((g, r))
        feats.append({"canonical_game_id": g, **f, "_context": f})
        his.append((g, _hi_raw(conn, g, hi_as_of if g == gid else min(hi_as_of, ct))))
    hi1 = {x["canonical_game_id"]: x for x in compute_handle_intensity_v1(feats)}
    for g, r in raws:
        p = hi1[g]["percentile_rank"]
        r["attention"] = None if p is None else (p - 50.0) / 50.0
    z = {c: slate_zscore([r[c] for _, r in raws]) for c in COMPONENTS}
    scores, cov = [], []
    for i, (g, r) in enumerate(raws):
        n = sum(1 for c in COMPONENTS if r[c] is not None)
        cov.append(n)
        scores.append(sum(z[c][i] for c in COMPONENTS) / n if n else 0.0)
    ti = [g for g, _ in raws].index(gid)
    pct = slate_percentile_ranks([abs(s) for s in scores])
    # totals Handle Intensity
    hz = {c: slate_zscore([(h or {}).get(c) for _, h in his]) for c in HI_COMPONENTS}
    hscores = []
    for i, (_, h) in enumerate(his):
        used = [hz[c][i] for c in HI_COMPONENTS if h and h.get(c) is not None]
        hscores.append(sum(used) / len(used) if used else None)
    valid = [s for s in hscores if s is not None]
    hpct = None
    if hscores[ti] is not None and len(valid) >= 2:
        hpct = 100.0 * sum(1 for s in valid if s <= hscores[ti]) / len(valid)
    s = scores[ti]
    return {"direction": "over" if s > 0 else ("under" if s < 0 else "even"), "score": s,
            "magnitude_percentile": pct[ti] if pct else None, "coverage": cov[ti],
            "input_status": "ABSTAIN" if cov[ti] < 2 else ("REDUCED_INPUT" if cov[ti] < 4 else "FULL"),
            "components": {c: raws[ti][1][c] for c in COMPONENTS},
            "handle_intensity_percentile": hpct, "handle_intensity_raw": his[ti][1], "slate_size": len(raws)}


# ------------------------------------------------------------------------------------------------ inference
def compute_totals_inference(conn, gid, as_of, cache=None):
    cache = {} if cache is None else cache
    sport, home, away, commence = _game(conn, gid)
    books = get_books_for_game(conn, gid)
    segs = resolve_segments(commence, as_of)
    for s in segs:
        s.update(aggregate_regime=None, breadth=None, per_book={}, tpp=None)
        if s["status"] == NOT_YET_OBSERVED:
            continue
        tpp = public_totals_pressure(conn, gid, s["start_label"], s["evaluation_horizon"], cache)
        s["tpp"] = tpp
        if tpp is None:
            continue
        p = tpp["direction"] if tpp["direction"] in ("over", "under") else "over"
        regimes, pro_moves, rec_moves = [], [], []
        for bk in books:
            try:
                a = market_state_as_of(conn, gid, s["start"], bk)
                b = market_state_as_of(conn, gid, s["evaluation_horizon"], bk)
            except PostKickoffCutoffError:
                s["per_book"][bk] = {"regime": "INSUFFICIENT", "reason": "post_kickoff_cutoff"}
                continue
            if a is None or b is None or a["total_points"] is None or b["total_points"] is None:
                s["per_book"][bk] = {"regime": "INSUFFICIENT", "reason": "missing_observation"}
                continue
            if is_stale(commence, s["evaluation_horizon"], b["market_observation_timestamp"]):
                s["per_book"][bk] = {"regime": "INSUFFICIENT", "reason": "stale"}
                continue
            pk = "total_price_over" if p == "over" else "total_price_under"
            d = (b["total_points"] - a["total_points"]) * (1 if p == "over" else -1)
            S = 1 if d > 0 else (-1 if d < 0 else 0)
            J = compute_J(a[pk], b[pk])
            reg = classify_regime(S, J)
            s["per_book"][bk] = {"regime": reg, "S": S, "J": J, "total_start": a["total_points"],
                                 "total_end": b["total_points"], "price_start": a[pk], "price_end": b[pk],
                                 "key_total_state": _key_state(sport, a["total_points"], b["total_points"])}
            regimes.append(reg)
            (pro_moves if bk in PRO_BOOKS else rec_moves).append(b["total_points"] - a["total_points"])
        s["aggregate_regime"], s["breadth"] = aggregate_book_regimes(regimes)
        s["sequencing"] = _sequencing(pro_moves, rec_moves)
    cur, fallback = select_current_segment(segs)
    out = {"version": VERSION, "segment": cur["name"] if cur else None, "side": None, "strength": None,
           "regime": cur["aggregate_regime"] if cur else None, "breadth": cur["breadth"] if cur else None,
           "tpp": cur["tpp"] if cur else None, "per_book": cur["per_book"] if cur else {},
           "sequencing": cur.get("sequencing") if cur else None, "fallback": fallback}
    if cur is None and not fallback:
        return dict(out, side="NOT_YET_EVALUABLE", reason="no evaluation segment has started (T-24h onward)")
    if cur is None or cur["tpp"] is None:
        return dict(out, side="INSUFFICIENT_EVIDENCE", reason="no usable segment or team inputs")
    tpp = cur["tpp"]
    usable = sum(1 for r in cur["per_book"].values() if r["regime"] != "INSUFFICIENT")
    if usable < 2:
        return dict(out, side="INSUFFICIENT_EVIDENCE", reason=f"{usable} usable book(s) (needs 2)")
    if tpp["input_status"] == "ABSTAIN":
        return dict(out, side="PASS", reason="abstain: fewer than 2 public-preference inputs available")
    mp = tpp["magnitude_percentile"]
    if tpp["direction"] == "even" or mp is None or mp <= 50:
        return dict(out, side="PASS", reason="no clear estimated public totals preference (magnitude <= slate median)")
    reg = cur["aggregate_regime"]
    base = {"RESISTANCE": "STRONG", "DIVERGENCE": "STRONG", "PARTIAL_DIVERGENCE": "MODERATE",
            "CONFIRMATION": "LEAN"}.get(reg)
    if base is None:
        return dict(out, side="PASS", reason=f"books {reg or 'not aggregated'}: no market-derived NEED")
    keys = [r.get("key_total_state") for r in cur["per_book"].values() if r.get("regime") != "INSUFFICIENT"]
    key_for_mod = [k if (k and "NO_KEY" not in k) else "NO_KEY_NUMBER_INVOLVEMENT" for k in keys]
    strength = apply_modifiers_and_cap(base, cur["breadth"], check_prior_contradiction(cur, segs), reg, key_for_mod,
                                       {"handle_intensity_percentile": tpp["handle_intensity_percentile"]})
    side = OPP[tpp["direction"]]
    why = (f"estimated public lean {tpp['direction'].upper()} (score {tpp['score']:+.2f}, {mp:.0f}th pct of slate, "
           f"{tpp['coverage']}/4 inputs); books {reg} ({cur['breadth']}); totals activity "
           f"{tpp['handle_intensity_percentile'] if tpp['handle_intensity_percentile'] is None else round(tpp['handle_intensity_percentile'])}"
           f"th pct -> market-derived NEED {side.upper()} ({strength})")
    return dict(out, side=side, strength=strength, reason=why, usable_books=usable)


def _sequencing(pro, rec):
    if not pro or not rec:
        return "UNCLEAR (needs both professional and recreational books)"
    mp, mr = st.median(pro), st.median(rec)
    if mp == 0 and mr == 0:
        return "NO MOVEMENT"
    if mp != 0 and mr == 0:
        return "PROFESSIONAL BOOKS MOVED, RECREATIONAL HELD"
    if mp == 0 and mr != 0:
        return "RECREATIONAL BOOKS MOVED, PROFESSIONAL HELD"
    return "SAME DIRECTION" if (mp > 0) == (mr > 0) else "OPPOSITE DIRECTIONS"
