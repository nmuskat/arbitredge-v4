"""
web_v4/sims_section.py -- the NFL SIMS block of the site snapshot (read-only, from validated SIMS exports).

Nothing here is computed from the synthetic sportsbook and nothing here feeds it: no import of sportsbook_v4,
no Need, no sharp-vs-public, no ledger. Values are passed through from the SIMS export unchanged (whitelisted
fields only); missing values stay null and are shown as a dash. The only derived numbers are counts (how many offers
were excluded for which data-quality reason). Odds age and "game started" are judged in the viewer's browser at view
time, so a page that sits open never presents old prices as current.
"""

from datetime import datetime, timezone

from sims_v5 import importer

OFFER_KEYS = ("rank", "slate_game", "kickoff_utc", "player_name", "team", "opponent", "market_label", "side",
              "book_title", "line", "price", "odds_at", "forecast_at", "mean", "median", "p10", "p90", "p_win",
              "p_win_decided", "break_even", "edge_pp", "roi_pct", "p_cal", "cal_roi_pct", "cons_roi_pct",
              "market_no_vig", "quality", "flags", "play_prob", "model_version")
TABLE_COLS = ("player_name", "team", "opponent", "market_label", "side", "line", "book_title", "price", "odds_at",
              "kickoff_utc", "p_win_decided", "p_win", "p_cal", "break_even", "roi_pct", "cal_roi_pct", "cons_roi_pct",
              "quality", "flags")             # the all-offers table (columnar: names once, values per row)
SHOWN_STATS = ("pass_yds", "pass_td", "rush_yds", "rec", "rec_yds")
SHOWN_DIST_KEYS = ("mean", "median", "p10", "p25", "p75", "p90")
META_KEYS = ("slate_date", "kind", "version", "status", "report_id", "rows_sha256", "created_at", "built_at",
             "model_versions", "n_sims", "outcome_sources", "books", "markets", "games", "games_with_odds",
             "offers_analyzed", "offers_valid", "picks", "oldest_odds", "newest_odds", "warnings", "excluded_games",
             "thresholds", "credits", "disclaimer")
ODDS_STALE_MIN = 90          # SIMS's own rule (edge engine max_odds_age_min); applied again in the browser
LABEL = ("MODELED: ArbitrEdge SIMS simulation outputs (separate system, read-only export). Probabilities and ROI are "
         "model estimates, not guarantees and not realized results. Not an instruction to wager.")


def _prov(doc):
    return {"file": doc.get("_file"), "exported_at": doc.get("exported_at"), "payload_sha256": doc.get("payload_sha256"),
            "sims_model_version": doc.get("sims_model_version"), "fixture": doc.get("fixture")}


def _edges(doc):
    b = doc["body"]
    m = b["meta"]
    rows = [dict(r, _id=i) for r, i in zip(b["rows"], b["row_ids"])]
    by_id = {r["_id"]: r for r in rows}
    top = [by_id[i] for i in b["top_row_ids"] if i in by_id]
    top.sort(key=lambda r: (r.get("rank") or 99))
    reasons = {}
    for r in rows:
        if r.get("quality") != "valid":
            for f in (r.get("flags") or ["no reason recorded"]):
                key = f.split(" (")[0]
                reasons[key] = reasons.get(key, 0) + 1
    trim = lambda r: {k: r.get(k) for k in OFFER_KEYS}
    rnd = lambda v: round(v, 4) if isinstance(v, float) else v          # display precision only (0.01%)
    return {**_prov(doc), "meta": {k: m.get(k) for k in META_KEYS}, "top": [trim(r) for r in top],
            "offers": _columnar(rows, rnd),
            "excluded_reasons": dict(sorted(reasons.items(), key=lambda x: -x[1]))}


def _columnar(rows, rnd):
    """{"cols", "dict", "rows"}: text values (and each flag) stored once in dict[col], rows hold their index."""
    vocab, out = {c: [] for c in TABLE_COLS}, []
    index = {c: {} for c in TABLE_COLS}

    def code(c, v):
        if v not in index[c]:
            index[c][v] = len(vocab[c])
            vocab[c].append(v)
        return index[c][v]
    for r in rows:
        row = []
        for c in TABLE_COLS:
            v = r.get(c)
            if c == "flags":
                row.append([code(c, f) for f in (v or [])])
            elif isinstance(v, str):
                row.append(code(c, v))
            else:
                row.append(rnd(v))
        out.append(row)
    return {"cols": list(TABLE_COLS), "dict": {c: v for c, v in vocab.items() if v}, "rows": out}


def _bins(d, n=20):
    """At most n bars from SIMS's own counts (exact tally or 30-bin histogram); counts are only grouped."""
    if not d:
        return None
    if d.get("type") == "exact":
        vals = d.get("values") or []
        if not vals:
            return None
        lo, hi = vals[0][0], vals[-1][0]
        if len(vals) <= n:
            return {"lo": lo, "hi": hi, "counts": [c for _, c in vals], "exact_values": [v for v, _ in vals]}
        w = (hi - lo) / n or 1
        out = [0] * n
        for v, c in vals:
            out[min(n - 1, int((v - lo) / w))] += c
        return {"lo": lo, "hi": hi, "counts": out}
    c = d.get("counts") or []
    k = -(-len(c) // n) if len(c) > n else 1
    return {"lo": d.get("lo"), "hi": d.get("hi"), "counts": [sum(c[i:i + k]) for i in range(0, len(c), k)]}


def _game(g):
    if g.get("forecast_status") != "official":
        return g
    pl = [{**{k: p.get(k) for k in ("player_id", "name", "team", "pos", "role", "play_prob", "anytime_td")},
           "stats": {st: {**{k: d.get(k) for k in SHOWN_DIST_KEYS}, "dist": _bins(d.get("dist"))}
                     for st, d in (p.get("stats") or {}).items() if st in SHOWN_STATS}}
          for p in g.get("players") or []]
    return {**{k: v for k, v in g.items() if k != "players"}, "players": pl}


def _projections(doc):
    b = doc["body"]
    return {**_prov(doc), "season": b.get("season"), "week": b.get("week"), "games": [_game(g) for g in b.get("games") or []]}


def _history(doc):
    b = doc["body"]
    t = b.get("top5") or {}
    return {**_prov(doc), "top5": {k: t.get(k) for k in ("overall", "voids", "by_market", "by_book", "by_probability", "warning")},
            "records": (b.get("top5_records") or [])[-100:]}


def block(sims_dir, now, allow_fixture=False):
    """The snapshot's "sims" entry. Never raises: any problem becomes a status the page shows."""
    if isinstance(now, (int, float)):
        now = datetime.fromtimestamp(now, timezone.utc)
    try:
        got = importer.load(sims_dir, now, allow_fixture=allow_fixture)
    except Exception as e:                               # noqa: BLE001  the site must build without SIMS
        return {"status": "ERROR", "detail": f"SIMS export could not be read ({type(e).__name__}); nothing is shown.",
                "label": LABEL}
    out = {"status": got["status"], "label": LABEL, "files": got["files"], "rejected": got["rejected"][-10:],
           "odds_stale_min": ODDS_STALE_MIN, "edges": None, "projections": None, "history": None}
    for k, f in (("edges", _edges), ("projections", _projections), ("history", _history)):
        if got[k]:
            try:
                out[k] = f(got[k])
            except (KeyError, TypeError, ValueError) as e:
                out["rejected"].append({"file": got[k].get("_file"), "reasons": [f"unexpected structure: {type(e).__name__}"]})
    out["fixture"] = next((out[k]["fixture"] for k in ("edges", "projections", "history") if out[k] and out[k]["fixture"]), None)
    if got["status"] == "MISSING":
        out["detail"] = "No SIMS export has been imported yet. Nothing is shown and nothing is substituted."
    elif got["status"] == "REJECTED":
        out["detail"] = "SIMS export files were found but none passed validation; nothing from them is shown."
    return out


def decode_offers(o):
    """The all-offers table back as a list of dicts (tests and tools)."""
    d = o.get("dict") or {}
    out = []
    for r in o["rows"]:
        x = {}
        for c, v in zip(o["cols"], r):
            if c == "flags":
                x[c] = [d["flags"][i] for i in v]
            elif c in d and isinstance(v, int) and not isinstance(v, bool):
                x[c] = d[c][v]
            else:
                x[c] = v
        out.append(x)
    return out
