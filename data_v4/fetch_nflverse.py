"""
data_v4/fetch_nflverse.py -- download nflverse research data with a manifest.

  python -m data_v4.fetch_nflverse --dest data/raw --seasons 2006-2026

Sources (public GitHub; no credentials, no Odds API credits):
  * games.csv  (nflverse/nfldata): schedule, final scores, a betting line
    per game (spread/total/moneyline; book and timestamp NOT specified --
    treated as an approximate consensus near close, never as an executable
    price), rest days, roof, weather, starting QBs.
  * play_by_play_<season>.parquet (nflverse/nflverse-data, release "pbp"):
    play-level EPA / success, used for team efficiency features.
Licence notes are recorded per file; see docs_v4/DATA_SOURCES.md.
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request

GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
PBP_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet"
LICENCE = {
    "games.csv": "nflverse/nfldata: no licence file in repository; public research data -- internal research use, "
                 "do not redistribute raw file or display its lines as ArbitrEdge market data",
    "pbp": "nflverse-data releases (nflfastR play-by-play); attribution to nflverse required; verify licence before "
           "any public redistribution",
}


def _download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "arbitredge-v4-research"})
    with urllib.request.urlopen(req, timeout=120) as r, open(path + ".part", "wb") as f:
        h, n = hashlib.sha256(), 0
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            h.update(b)
            n += len(b)
    os.replace(path + ".part", path)
    return h.hexdigest(), n


def fetch(dest, seasons):
    os.makedirs(dest, exist_ok=True)
    man_path = os.path.join(dest, "MANIFEST.json")
    manifest = json.load(open(man_path)) if os.path.exists(man_path) else {}
    jobs = [("games.csv", GAMES_URL, "games.csv")]
    jobs += [(f"play_by_play_{s}.parquet", PBP_URL.format(season=s), "pbp") for s in seasons]
    for name, url, lic in jobs:
        path = os.path.join(dest, name)
        try:
            sha, n = _download(url, path)
        except Exception as e:  # a missing future season is normal; record and continue
            print(f"skip {name}: {e}", file=sys.stderr)
            continue
        manifest[name] = {"url": url, "sha256": sha, "bytes": n,
                          "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "licence": LICENCE[lic]}
        print(f"{name}: {n} bytes sha256 {sha[:12]}")
    with open(man_path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default="data/raw")
    ap.add_argument("--seasons", default="2006-2026")
    a = ap.parse_args(argv)
    lo, hi = (int(x) for x in a.seasons.split("-"))
    fetch(a.dest, range(lo, hi + 1))


if __name__ == "__main__":
    main()
