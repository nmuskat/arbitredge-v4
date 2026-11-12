"""
market_intel/engine.py -- one Market Intelligence card per upcoming game: SPREAD (original book_need.inference,
unchanged) and TOTALS (market_intel.totals = mi_totals.v1), evaluated at the build time from already-collected data.
"""
from market_intel import compat  # noqa: F401  (installs the stand-ins for the original's absent db/config modules)
from market_intel.compat import build, iso

EVAL_WINDOW_S = 30 * 3600            # the original evaluates from T-24h; earlier games are NOT_YET_EVALUABLE


def _book_view(per_book, kind):
    out = {}
    for b, x in (per_book or {}).items():
        d = {"regime": x.get("regime")}
        if x.get("reason"):
            d["reason"] = x["reason"]
        if kind == "spread" and x.get("key_number_state"):
            d["key"] = x["key_number_state"]
        if kind == "total":
            for k in ("total_start", "total_end", "price_start", "price_end", "key_total_state"):
                if x.get(k) is not None:
                    d[k] = x[k]
        out[b] = d
    return out


def spread_card(conn, gid, as_of):
    from book_need.inference import compute_inference
    r = compute_inference(conn, gid, as_of)
    cur = next((s for s in r["segments"] if s["name"] == r["current_segment_name"]), None)
    pp = r["public_pressure"] or {}
    side = r["inferred_book_need_side"]
    return {"engine": "original book_need_week1_v1 (unchanged)", "need": side if side in ("home", "away") else None,
            "decision": side.upper() if side in ("home", "away") else side, "strength": r["need_strength"],
            "data_quality": r["data_quality"], "segment": r["current_segment_name"],
            "public_side_estimate": pp.get("side_preference_direction"),
            "public_magnitude_pct": pp.get("side_preference_magnitude_percentile"),
            "handle_intensity_pct": pp.get("handle_intensity_percentile"),
            "regime": cur.get("aggregate_regime") if cur else None, "breadth": cur.get("breadth") if cur else None,
            "contradiction": r["contradiction_flag"], "books": _book_view(cur.get("per_book") if cur else {}, "spread")}


def totals_card(conn, gid, as_of, cache):
    from market_intel.totals import compute_totals_inference
    r = compute_totals_inference(conn, gid, as_of, cache)
    tpp = r.get("tpp") or {}
    return {"engine": r["version"], "need": r["side"] if r["side"] in ("over", "under") else None,
            "decision": r["side"].upper() if r["side"] in ("over", "under") else r["side"], "strength": r.get("strength"),
            "reason": r.get("reason"), "segment": r.get("segment"), "public_lean_estimate": tpp.get("direction"),
            "public_magnitude_pct": tpp.get("magnitude_percentile"), "inputs": tpp.get("input_status"),
            "components": tpp.get("components"), "activity_pct": tpp.get("handle_intensity_percentile"),
            "regime": r.get("regime"), "breadth": r.get("breadth"), "sequencing": r.get("sequencing"),
            "books": _book_view(r.get("per_book"), "total")}


def cards(store_conn, finals, polls, now, bconn=None):
    """{sport: [card]} for games kicking off in (now, now + 30 h]; every error is reported on its card.
    bconn: this build's synthetic-book database (read-only) for the Synthetic Sharp Agreement panel."""
    from market_intel import sharp as SH
    conn, ids = build(store_conn, finals, polls)
    latest = store_conn.execute("SELECT MAX(fetched_at) FROM mkt_quote WHERE fetched_at<=?", (now,)).fetchone()[0]
    data_through = iso(latest) if latest else None
    out, cache = {"nfl": [], "cfb": []}, {}
    for eid, (gid, sp, home, away, ko) in sorted(ids.items(), key=lambda kv: kv[1][4]):
        if not (now < ko <= now + EVAL_WINDOW_S):
            continue
        c = {"event_id": eid, "game": f"{away} @ {home}", "kickoff": iso(ko), "as_of": iso(now)}
        for name, fn in (("spread", lambda: spread_card(conn, gid, iso(now))),
                         ("total", lambda: totals_card(conn, gid, iso(now), cache))):
            try:
                c[name] = fn()
            except Exception as e:                                   # noqa: BLE001  (shown, never hidden)
                c[name] = {"decision": "ERROR", "reason": f"{type(e).__name__}: {e}"[:200]}
            try:
                c[name].update(SH.card(bconn, sp, eid, name, iso(now), c[name].get("need"), data_through))
            except Exception as e:                                   # noqa: BLE001
                c[name]["agreement"] = {"category": "ERROR", "why": f"{type(e).__name__}: {e}"[:200]}
        out[sp].append(c)
    return out
