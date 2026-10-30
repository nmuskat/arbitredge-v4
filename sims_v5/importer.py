"""
sims_v5/importer.py -- pick the newest VALID SIMS export of each kind; never raises, never repairs.

  load(directory, now, allow_fixture=False) -> {"status", "projections", "edges", "history", "rejected", "files"}
  python -m sims_v5.importer accept --in NEW --have HAVE --out OK
      (build step: copy new export files that validate into OK; print why others were rejected; exit 0 always)

status: OK (at least one valid file), MISSING (no export files at all), REJECTED (files exist, none valid).
"""

import argparse
import glob
import gzip
import json
import os
import shutil
import sys
from datetime import datetime, timezone

from sims_v5 import schema

PATTERN = "sims_nfl_*.json.gz"


def read(path, now, allow_fixture):
    """(doc or None, problems)."""
    try:
        if os.path.getsize(path) > schema.MAX_COMPRESSED:
            return None, ["file larger than 5 MB"]
        with gzip.open(path, "rt") as f:
            text = f.read(schema.MAX_JSON + 1)
        if len(text) > schema.MAX_JSON:
            return None, ["file larger than 50 MB uncompressed"]
        doc = json.loads(text)
    except (OSError, EOFError, ValueError, UnicodeDecodeError) as e:
        return None, [f"unreadable: {type(e).__name__}"]
    problems = schema.validate(doc, now, allow_fixture)
    return (None if problems else doc), problems


def load(directory, now=None, allow_fixture=False):
    now = now or datetime.now(timezone.utc)
    paths = sorted(glob.glob(os.path.join(directory, "**", PATTERN), recursive=True)) if directory else []
    out = {"status": "MISSING", "files": len(paths), "rejected": [], "projections": None, "edges": None, "history": None}
    for p in paths:
        doc, problems = read(p, now, allow_fixture)
        if doc is None:
            out["rejected"].append({"file": os.path.basename(p), "reasons": problems[:5]})
            continue
        doc["_file"] = os.path.basename(p)
        k = doc["kind"]
        if out[k] is None or doc["exported_at"] >= out[k]["exported_at"]:
            out[k] = doc
    if any(out[k] for k in schema.KINDS):
        out["status"] = "OK"
    elif paths:
        out["status"] = "REJECTED"
    return out


def accept(new_dir, have_dir, out_dir, now=None):
    """Copy new valid export files to out_dir (never overwriting); returns (accepted, rejected)."""
    now = now or datetime.now(timezone.utc)
    os.makedirs(out_dir, exist_ok=True)
    have = {os.path.basename(p) for p in glob.glob(os.path.join(have_dir or "", PATTERN))}
    acc, rej = [], []
    for p in sorted(glob.glob(os.path.join(new_dir, PATTERN))):
        name = os.path.basename(p)
        if name in have or os.path.exists(os.path.join(out_dir, name)):
            continue
        doc, problems = read(p, now, allow_fixture=False)
        if doc is None:
            rej.append((name, problems))
            continue
        shutil.copyfile(p, os.path.join(out_dir, name))
        acc.append(name)
    return acc, rej


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("accept",))
    ap.add_argument("--in", dest="new", required=True)
    ap.add_argument("--have", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    acc, rej = accept(a.new, a.have, a.out)
    print(json.dumps({"accepted": acc, "rejected": [{"file": n, "reasons": r} for n, r in rej]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
