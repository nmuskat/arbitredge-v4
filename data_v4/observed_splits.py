"""
data_v4/observed_splits.py -- REAL, third-party betting-split observations (OBSERVED), for calibration only.

Kept completely apart from ArbitrEdge's simulated (MODELED) splits:
  * separate module, separate storage (release `v4-observed-splits`), separate label OBSERVED_THIRD_PARTY;
  * nothing in the synthetic sportsbook reads these records; only the offline calibration tool does.

Collection is MANUAL ENTRY by the owner from pages they view normally (e.g. a sportsbook's published
splits), or a licensed feed if one is ever approved. No scraping and no bypassing access controls.

One record = one selection of one market at one moment:
  sport, home, away, kickoff_et_date, sportsbook, aggregation, market, selection, line, ticket_pct,
  handle_pct, observed_at, source, source_url, collection_method, entered_by, notes
A blank percentage stays blank: never filled in, never inferred from the other side.

    python -m data_v4.observed_splits validate observed.csv
    python -m data_v4.observed_splits import observed.csv --out DIR    (writes an immutable file)
"""

import argparse
import csv
import datetime
import gzip
import json
import os
import sys
import time

from sportsbook_v4.util import json_hash, to_epoch

FORMAT = "v4_observed_splits_1"
LABEL = "OBSERVED_THIRD_PARTY"
FIELDS = ("sport", "home", "away", "kickoff_et_date", "sportsbook", "aggregation", "market", "selection", "line",
          "ticket_pct", "handle_pct", "observed_at", "source", "source_url", "collection_method", "entered_by", "notes")
REQUIRED = ("sport", "home", "away", "kickoff_et_date", "sportsbook", "aggregation", "market", "selection",
            "observed_at", "source", "collection_method")
SPORTS = ("nfl", "cfb")
AGGREGATION = ("single_book", "multiple_books", "unknown")
MARKETS = {"spread": ("home", "away"), "total": ("over", "under"), "moneyline": ("home", "away")}
METHODS = ("manual_entry", "licensed_feed")
SUM_TOLERANCE = 2.0         # published two-way splits are rounded; both sides must sum to 100 +- 2


class InvalidObservation(ValueError):
    pass


def _pct(v, name):
    if v in (None, ""):
        return None
    try:
        x = float(v)
    except ValueError:
        raise InvalidObservation(f"{name} is not a number: {v!r}")
    if not 0.0 <= x <= 100.0:
        raise InvalidObservation(f"{name} out of range 0-100: {x}")
    return x


def normalize(row):
    """One validated record (raises InvalidObservation). Percentages stay None when blank."""
    r = {k: (row.get(k) or "").strip() for k in FIELDS}
    for k in REQUIRED:
        if not r[k]:
            raise InvalidObservation(f"missing {k}")
    r["sport"], r["market"], r["selection"] = r["sport"].lower(), r["market"].lower(), r["selection"].lower()
    r["sportsbook"], r["aggregation"] = r["sportsbook"].lower(), r["aggregation"].lower()
    if r["sport"] not in SPORTS:
        raise InvalidObservation(f"sport must be one of {SPORTS}")
    if r["market"] not in MARKETS or r["selection"] not in MARKETS[r["market"]]:
        raise InvalidObservation(f"{r['market']}/{r['selection']} is not a valid market/selection")
    if r["aggregation"] not in AGGREGATION:
        raise InvalidObservation(f"aggregation must be one of {AGGREGATION}")
    if r["collection_method"] not in METHODS:
        raise InvalidObservation(f"collection_method must be one of {METHODS} (no scraping)")
    try:
        datetime.date.fromisoformat(r["kickoff_et_date"])
    except ValueError:
        raise InvalidObservation("kickoff_et_date must be YYYY-MM-DD (Eastern date of kickoff)")
    try:
        r["observed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(to_epoch(r["observed_at"])))
    except Exception:
        raise InvalidObservation("observed_at must be an ISO time with a zone, e.g. 2026-11-01T16:05:00Z")
    r["ticket_pct"], r["handle_pct"] = _pct(r["ticket_pct"], "ticket_pct"), _pct(r["handle_pct"], "handle_pct")
    if r["ticket_pct"] is None and r["handle_pct"] is None:
        raise InvalidObservation("at least one of ticket_pct / handle_pct is required")
    r["line"] = float(r["line"]) if r["line"] else None
    r["label"] = LABEL
    r["observation_id"] = json_hash([r[k] for k in ("sport", "home", "away", "kickoff_et_date", "sportsbook", "market",
                                                    "selection", "observed_at")])[:32]
    return r


def check_pairs(records):
    """Both sides of the same market, book and moment must sum to about 100 where both are given."""
    groups = {}
    for r in records:
        k = (r["sport"], r["home"], r["away"], r["kickoff_et_date"], r["sportsbook"], r["market"], r["observed_at"])
        groups.setdefault(k, []).append(r)
    problems = []
    for k, rs in groups.items():
        sels = {r["selection"] for r in rs}
        if len(sels) != len(rs):
            problems.append(f"{k}: the same selection entered twice")
        if len(rs) == 2:
            for f in ("ticket_pct", "handle_pct"):
                a, b = rs[0][f], rs[1][f]
                if a is not None and b is not None and abs(a + b - 100.0) > SUM_TOLERANCE:
                    problems.append(f"{k}: {f} sides sum to {a + b:g}, not 100")
    return problems


def read_csv(path):
    out, errors = [], []
    with open(path, newline="") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            if not any((v or "").strip() for v in row.values()):
                continue
            try:
                out.append(normalize(row))
            except InvalidObservation as e:
                errors.append(f"line {i}: {e}")
    errors += check_pairs(out)
    return out, errors


def write(records, out_dir, now=None):
    now = now or time.time()
    os.makedirs(out_dir, exist_ok=True)
    name = f"observed_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))}.json.gz"
    path = os.path.join(out_dir, name)
    if os.path.exists(path):
        raise FileExistsError(name)
    doc = {"format": FORMAT, "label": LABEL, "imported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
           "records": records, "records_hash": json_hash(records)}
    with gzip.open(path, "wt") as f:
        json.dump(doc, f, sort_keys=True)
    return path


def load(directory):
    """All stored observations, hash-verified; a re-entered identical observation counts once."""
    seen, out = set(), []
    for root, _dirs, files in os.walk(directory or ""):
        for fn in sorted(files):
            if not (fn.startswith("observed_") and fn.endswith(".json.gz")):
                continue
            with gzip.open(os.path.join(root, fn), "rt") as f:
                doc = json.load(f)
            if json_hash(doc["records"]) != doc["records_hash"] or doc.get("label") != LABEL:
                raise ValueError(f"{fn}: observed-splits file failed verification")
            for r in doc["records"]:
                if r["observation_id"] not in seen:
                    seen.add(r["observation_id"])
                    out.append(r)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("validate", "import"))
    ap.add_argument("csv")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    records, errors = read_csv(a.csv)
    for e in errors:
        print("ERROR", e)
    print(f"{len(records)} valid observation(s), {len(errors)} problem(s)")
    if errors:
        return 1
    if a.command == "import":
        if not a.out:
            print("--out is required for import")
            return 2
        print("saved", write(records, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
