"""
original_v3/replay.py -- run the ORIGINAL ArbitrEdge Inferred Book Need pipeline (book_need.inference.compute_inference,
unchanged) on the V4 data via original_v3.adapter, at historical decision times, and save every output.

  python -m original_v3.replay --market-db DB --rankings DIR --results DIR --out FILE [--workers 4] [--cadence v3|v4]

--cadence v3 (default) = the original staleness tiers exactly (operations/cadence_config: <=20 min required in the
final hour, <=60 min 1-6 h out). --cadence v4 = ADAPTER VARIANT, clearly labelled: the same rule with the
tolerance widened to V4's actual collection cadence (180 min in the final 6 h), since V4 never polled every 20 min.
"""
import argparse
import json
import multiprocessing as mp
import os
import pickle

AS_OF = {"T-6h": 6 * 3600, "T-1h": 3600}
_DB = {}


def _init(dbfile, cadence):
    import sqlite3
    import original_v3.adapter  # noqa: F401  (installs the db.migrate stand-in)
    _DB["conn"] = sqlite3.connect(f"file:{dbfile}?mode=ro", uri=True)
    if cadence == "v4":
        from operations import cadence_config as CC
        CC.ODDS_PROXIMITY_TIERS[:] = [("very_low", None, 72, 1440), ("low_moderate", 72, 24, 960),
                                      ("elevated", 24, 6, 480), ("high", 6, 1, 180), ("highest", 1, 0, 180)]


def _one(args):
    gid, ko_iso, label, as_of = args
    from book_need.inference import compute_inference
    try:
        r = compute_inference(_DB["conn"], gid, as_of)
    except Exception as e:                                         # reported, never hidden
        return {"gid": gid, "as_of_label": label, "error": f"{type(e).__name__}: {e}"}
    seg = [s for s in r["segments"] if s.get("aggregate_regime")]
    return {"gid": gid, "as_of_label": label, "as_of": as_of, "side": r["inferred_book_need_side"],
            "strength": r["need_strength"], "quality": r["data_quality"], "segment": r["current_segment_name"],
            "fallback": r["fallback_occurred"], "contradiction": r["contradiction_flag"],
            "public_pressure": r["public_pressure"],
            "regime": next((s["aggregate_regime"] for s in r["segments"] if s["name"] == r["current_segment_name"]), None),
            "breadth": next((s["breadth"] for s in r["segments"] if s["name"] == r["current_segment_name"]), None),
            "per_book": next(({b: {k: v for k, v in x.items() if k in ("regime", "S", "J", "key_number_state", "reason")}
                               for b, x in s["per_book"].items()} for s in r["segments"]
                              if s["name"] == r["current_segment_name"]), {}),
            "segments_regimes": {s["name"]: s.get("aggregate_regime") for s in r["segments"]}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    for k in ("market-db", "rankings", "results", "out"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--cadence", default="v3", choices=["v3", "v4"])
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)
    import sqlite3
    from collect_v4 import backfill as B
    from intel_v7.evaluate import load_finals
    from intel_v7.history import games
    from original_v3.adapter import build, iso
    from tools_v4.backfill_replay import load_polls
    src = sqlite3.connect(f"file:{a.market_db}?mode=ro", uri=True)
    end = src.execute("SELECT MAX(fetched_at) FROM mkt_quote").fetchone()[0]
    gl = [(s, e, k, h, aw) for s in ("nfl", "cfb") for e, k, h, aw in games(src, s)]
    finals = load_finals(a.rankings, a.results, gl)
    polls = B.poll_snapshots(load_polls(a.rankings))
    dbfile = a.out + ".v3.db"
    if os.path.exists(dbfile):
        os.remove(dbfile)
    conn, ids = build(a.market_db, finals, polls, dbfile)
    jobs = []
    for eid, (gid, sp, h, aw, ko) in ids.items():
        if ko > end or (sp, eid) not in finals:
            continue                                               # completed games with a final score only
        for label, off in AS_OF.items():
            jobs.append((gid, iso(ko), label, iso(ko - off)))
    jobs = jobs[:a.limit] if a.limit else jobs
    with mp.Pool(a.workers, initializer=_init, initargs=(dbfile, a.cadence)) as pool:
        res = list(pool.imap_unordered(_one, jobs, chunksize=4))
    meta = {gid: {"event_id": eid, "sport": sp, "home": h, "away": aw, "kickoff": ko}
            for eid, (gid, sp, h, aw, ko) in ids.items()}
    pickle.dump({"results": res, "meta": meta, "cadence": a.cadence, "finals": finals}, open(a.out, "wb"))
    print(len(res), "inferences;", sum(1 for r in res if "error" in r), "errors")


if __name__ == "__main__":
    main()
