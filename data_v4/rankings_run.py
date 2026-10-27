"""
python -m data_v4.rankings_run --out DIR    (GitHub Actions: .github/workflows/cfb-rankings.yml)

Fetches one AP Top-25 snapshot (+ FBS team list) from CollegeFootballData
with the key in env CFBD_API_KEY and writes rankings_<UTC>.json. The key is
never printed; errors are reported without it.
"""

import argparse
import json
import os
import sys
import time

from data_v4 import rankings


def season_for(t):
    g = time.gmtime(t)
    return g.tm_year if g.tm_mon >= 3 else g.tm_year - 1        # January bowls belong to the previous season


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    key = os.environ.get("CFBD_API_KEY", "").strip()
    if not key:
        print("::error::CFBD_API_KEY secret is not available to this job")
        return 1
    now = time.time()
    try:
        snap = rankings.fetch(season_for(now), key, now=now)
    except Exception as e:  # never echo the key
        print("::error::CFBD request failed: " + str(e).replace(key, "[REDACTED]"))
        return 1
    if not any(p["ranks"] for p in snap["polls"]):
        print("::warning::no AP poll returned yet for this season; nothing saved")
        return 0
    os.makedirs(a.out, exist_ok=True)
    name = f"rankings_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))}.json"
    with open(os.path.join(a.out, name), "w") as f:
        json.dump(snap, f, sort_keys=True)
    latest = max(snap["polls"], key=lambda p: ({"regular": 0, "postseason": 1}.get(p["season_type"], 0), p["week"] or 0))
    print(json.dumps({"saved": name, "teams": len(snap["teams"]), "latest_week": latest["week"],
                      "season_type": latest["season_type"], "top5": [r["school"] for r in latest["ranks"][:5]]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
