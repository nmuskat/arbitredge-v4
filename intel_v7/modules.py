"""
intel_v7/modules.py -- market-intelligence modules A (divergence), B (active resistance), C (key-number defense).
Module D (convergence) is in convergence.py, E (classifier) in classify.py.

All take a History and a decision time t and use ONLY fetches at or before t. Directions are RELATIVE to the retail
consensus: "pro leans home" means professional pricing rates home higher than the retail books do. A lean is a
pricing observation, never a sportsbook's liability.
"""
from intel_v7.config import CONFIG
from intel_v7.history import SIDES, operator_of, view


def side_of(market, direction):
    """direction > 0: higher home margin / higher total."""
    a, b = SIDES[market]
    return a if direction > 0 else b


def freshness(hist, t):
    ft, books = hist.snap_at(t)
    if ft is None:
        return {"status": "NO_DATA", "fetch_at": None}
    age = t - ft
    v = view(books)
    ok = age <= CONFIG["max_decision_fetch_age_s"]
    return {"status": "FRESH" if ok else "STALE", "fetch_at": ft, "age_min": round(age / 60, 1),
            "pro_quoted": sorted(v["pro"]), "retail_quoted": v["retail_books"],
            "note": "a book counts only if quoted in this fetch; prices may have changed since (unobserved)"}


# ------------------------------------------------------------------------------------------------ A. divergence
def divergence(hist, t):
    ft, books = hist.snap_at(t)
    v = view(books)
    if "pro_mu" not in v or "retail_mu" not in v:
        why = "no professional operator quoted in the latest fetch" if "pro_mu" not in v else \
              f"fewer than {CONFIG['min_retail_books']} retail books quoted"
        return {"status": "INSUFFICIENT", "reason": why}
    thr = max(CONFIG["gap_material_pts"], CONFIG["gap_min_uncertainty_multiple"] * v["retail_disp"])
    gap = v["pro_mu"] - v["retail_mu"]
    per_op = {op: round(mu - v["retail_mu"], 3) for op, mu in v["pro"].items()}
    out = {"status": "NOT_MATERIAL", "gap_pts": round(gap, 3), "threshold_pts": round(thr, 3),
           "pro_basis": v["pro_basis"], "pro_mu": round(v["pro_mu"], 3), "retail_mu": round(v["retail_mu"], 3),
           "retail_dispersion_pts": round(v["retail_disp"], 3), "retail_n": v["retail_n"], "gap_by_operator": per_op,
           "book_margins": {bk: round(_overround(q["price_a"], q["price_b"]), 4) for bk, q in books.items()},
           "quotes": {bk: {"point": q["point"], "price_a": q["price_a"], "price_b": q["price_b"],
                           "fair_mu": None if q["mu"] is None else round(q["mu"], 3)} for bk, q in books.items()}}
    material = {op: g for op, g in per_op.items() if abs(g) >= thr}
    signs = {1 if g > 0 else -1 for g in material.values()}
    if len(signs) > 1:
        out.update(status="PROS_DISAGREE")
        return out
    if abs(gap) >= thr:
        both = len(v["pro"]) == 2 and all(abs(g) >= thr and (g > 0) == (gap > 0) for g in per_op.values())
        out.update(status="MATERIAL", side=side_of(hist.market, gap), corroborated=both)
    return out


def _overround(pa, pb):
    imp = lambda p: 100.0 / (p + 100.0) if p > 0 else -p / (-p + 100.0)    # noqa: E731
    return imp(pa) + imp(pb) - 1.0


# ------------------------------------------------------------------------------------------------ B. resistance
def resistance(hist, t):
    f1, books1 = hist.snap_at(t)
    if f1 is None:
        return {"status": "INDETERMINATE", "reason": "no data"}
    window = [(ft, view(b)) for ft, b in hist.snaps_between(f1 - CONFIG["resistance_window_s"], f1)]
    window = [(ft, v) for ft, v in window if "retail_mu" in v]
    if len(window) < 2:
        return {"status": "INDETERMINATE", "reason": "fewer than 2 fetches with a retail consensus in the window "
                                                     "(collection cadence too sparse to observe resistance)"}
    (f0, v0), (f1b, v1) = window[0], window[-1]
    rm = v1["retail_mu"] - v0["retail_mu"]
    ops = {}
    for op in CONFIG["pro_operators"]:
        quoted = [v["pro"][op] for _, v in window if op in v["pro"]]
        present = op in v0["pro"] and op in v1["pro"]
        pm = v1["pro"][op] - v0["pro"][op] if present else None
        ops[op] = {"present_start_end": present, "fetches_quoted": len(quoted), "move_pts": None if pm is None else round(pm, 3),
                   "change_windows": _change_windows(hist, op, f0, f1b)}
    base = {"window": [f0, f1b], "fetches": len(window), "retail_move_pts": round(rm, 3), "operators": ops}
    usable = {op: o for op, o in ops.items() if o["present_start_end"] and o["fetches_quoted"] >= CONFIG["min_window_fetches"]}
    if abs(rm) < CONFIG["retail_move_pts"]:
        led = {op: o["move_pts"] for op, o in usable.items() if abs(o["move_pts"]) >= CONFIG["retail_move_pts"]}
        dirs = {1 if m > 0 else -1 for m in led.values()}
        if len(dirs) == 1:
            return {**base, "status": "PRO_LED_MOVE", "side": side_of(hist.market, next(iter(dirs))),
                    "reason": "professional price moved while the retail consensus did not (who moved first inside "
                              "a fetch interval is unknown)"}
        return {**base, "status": "NO_RETAIL_MOVE"}
    if not usable:
        return {**base, "status": "INDETERMINATE", "reason": "professional quotes missing at the start/end of the window "
                                                            "(possibly stale): resistance cannot be established"}
    held = {op for op, o in usable.items() if abs(o["move_pts"]) <= CONFIG["pro_hold_pts"] or (o["move_pts"] * rm < 0)}
    followed = set(usable) - held
    against = side_of(hist.market, -rm)
    if held and not followed:
        st = "ACTIVE_RESISTANCE" if len(held) == 2 else "RESISTANCE_UNCORROBORATED"
        return {**base, "status": st, "side": against, "held_by": sorted(held),
                "reason": "retail moved %.2f pts; professional price held or moved the other way while quoted in "
                          "every fetch of the window" % rm}
    if held and followed:
        return {**base, "status": "INDETERMINATE", "held_by": sorted(held), "followed_by": sorted(followed),
                "reason": "one professional operator held while the other followed: a single held quote may be a "
                          "stale feed"}
    return {**base, "status": "FOLLOWED", "reason": "professional prices moved with the retail books"}


def _change_windows(hist, op, t0, t1):
    """Intervals [previous fetch, fetch] within which this operator's price is known to have changed."""
    out, prev = [], None
    for ft, books in hist.snaps_between(t0, t1):
        q = [b for bk, b in books.items() if operator_of(bk) == op and b["mu"] is not None]
        if not q:
            continue
        key = tuple(sorted((b["point"], b["price_a"], b["price_b"]) for b in q))
        if prev is not None and key != prev[1]:
            out.append([prev[0], ft])
        prev = (ft, key)
    return out


# ------------------------------------------------------------------------------------------------ C. key numbers
def key_numbers(hist, t):
    if not (hist.sport == "nfl" and hist.market == "spread"):
        return {"status": "NOT_APPLICABLE", "reason": "key-number analysis applies to NFL spreads"}
    f1, books1 = hist.snap_at(t)
    v1 = view(books1)
    pro_books = [bk for bk in ("pinnacle", "betonlineag", "lowvig") if bk in books1]
    if not pro_books or "retail_mu" not in v1:
        return {"status": "INSUFFICIENT", "reason": "no professional or retail consensus quote at the decision fetch"}
    retail_points = sorted(books1[b]["point"] for b in CONFIG["retail_books"] if b in books1)
    per = {}
    for bk in pro_books:
        q = books1[bk]
        p = q["point"]
        hist_q = [(ft, b[bk]) for ft, b in hist.snaps_between(f1 - CONFIG["resistance_window_s"], f1) if bk in b]
        line_ch = sum(1 for (_, x), (_, y) in zip(hist_q, hist_q[1:]) if x["point"] != y["point"])
        juice_ch = sum(1 for (_, x), (_, y) in zip(hist_q, hist_q[1:]) if x["point"] == y["point"] and
                       (x["price_a"], x["price_b"]) != (y["price_a"], y["price_b"]))
        held_key = abs(p) in CONFIG["key_numbers"] and len(hist_q) >= 2 and all(x["point"] == p for _, x in hist_q)
        shift = None
        if len(hist_q) >= 2 and hist_q[0][1]["point"] == p:
            shift = _nv(q) - _nv(hist_q[0][1])       # change in home no-vig probability at the same number
        per[bk] = {"point": p, "on_key": abs(p) in CONFIG["key_numbers"], "on_secondary": abs(p) in CONFIG["secondary_numbers"],
                   "held_key_through_window": held_key, "home_novig_shift_at_number": None if shift is None else round(shift, 4),
                   "line_changes": line_ch, "juice_only_changes": juice_ch, "observed_fetches": len(hist_q)}
    out = {"status": "NO_DEFENSE", "retail_points": retail_points, "pro": per}
    defenders = [bk for bk, x in per.items() if x["held_key_through_window"]
                 and any(rp != x["point"] for rp in retail_points)
                 and x["home_novig_shift_at_number"] is not None and abs(x["home_novig_shift_at_number"]) >= CONFIG["key_juice_shift"]]
    if defenders:
        gap = v1["pro_mu"] - v1["retail_mu"] if "pro_mu" in v1 else 0.0
        # juice moved but the line held at the key while retail moved off it. The evidence is directional only if
        # the professional fair (juice included) still sits on the other side of the retail consensus.
        if abs(gap) >= CONFIG["pro_hold_pts"]:
            out.update(status="KEY_DEFENSE", side=side_of("spread", gap), defenders=defenders,
                       reason="professional line held at the key number with juice adjusted while retail moved off it; "
                              "the juice-adjusted professional price still differs from retail by %.2f pts" % gap)
        else:
            out.update(status="KEY_HELD_JUICE_ONLY", defenders=defenders,
                       reason="line held at the key number but the juice change brought the price level with retail: "
                              "no directional preference")
    return out


def _nv(q):
    imp = lambda p: 100.0 / (p + 100.0) if p > 0 else -p / (-p + 100.0)    # noqa: E731
    a, b = imp(q["price_a"]), imp(q["price_b"])
    return a / (a + b)
