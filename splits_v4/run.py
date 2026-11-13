"""
splits_v4/run.py -- the hourly splits check (run by .github/workflows/splits-collect.yml; no secrets, no API calls).

  python -m splits_v4.run --state collector_state.json --rankings-dir DIR --existing names.txt --out DIR [--slate EPOCH]

Decides whether a slate is due (splits_v4.slates); if so takes the snapshot and writes
DIR/splits_<taken UTC>_S<slate epoch>.json. A slate already captured (a v4-splits file name carrying _S<slate>) is
never taken twice. Writes taken=true|false to $GITHUB_OUTPUT.
"""
import argparse
import json
import os
import re
import time

from splits_v4 import slates, snapshot


def done_slates(names):
    return [int(m.group(1)) for n in names for m in [re.search(r"_S(\d{9,11})\.json$", n.strip())] if m]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--rankings-dir", default=None)
    ap.add_argument("--existing", default=None, help="names of files already in release v4-splits")
    ap.add_argument("--out", required=True)
    ap.add_argument("--slate", type=float, default=None, help="force this slate (manual run)")
    a = ap.parse_args(argv)
    now = time.time()
    state = json.load(open(a.state))
    from collect_v4.actions_run import load_rankings
    ranks = load_rankings(a.rankings_dir) if a.rankings_dir else []
    names = open(a.existing).read().split() if a.existing and os.path.exists(a.existing) else []
    if a.slate:
        slate, timing = a.slate, "MANUAL"
    else:
        slate, timing = slates.due(now, slates.tracked_kickoffs(state, ranks, now), done_slates(names))
    taken = False
    if slate is None:
        print(f"no snapshot: {timing}")
    else:
        doc = snapshot.take(state, slate, timing, now)
        os.makedirs(a.out, exist_ok=True)
        path = os.path.join(a.out, f"splits_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))}_S{int(slate)}.json")
        with open(path, "w") as fh:
            json.dump(doc, fh, separators=(",", ":"), sort_keys=True)
        taken = True
        print(f"splits snapshot: slate {doc['slate_iso']} ({timing}), {len(doc['events'])} games; " +
              "; ".join(f"{k} {v['status'][:20]} matched {v['matched']}/{v['rows']}" for k, v in doc["sources"].items()))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"taken={'true' if taken else 'false'}\n")


if __name__ == "__main__":
    main()
