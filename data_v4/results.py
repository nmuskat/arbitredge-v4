"""
data_v4/results.py -- final scores for the Book Need Tracker (no Odds API credits).

  NFL  nflverse games.csv (public, no key): scores appear once a game is played.
  CFB  CollegeFootballData /games (existing free-tier key, owner-approved 2026-10-29): `completed` flag.

    python -m data_v4.results --sport nfl --existing DIR --out DIR
    python -m data_v4.results --sport cfb --existing DIR --out DIR     (needs CFBD_API_KEY)

Each fetch is saved as an immutable results_<sport>_<UTC>.json.gz, only when the set of final scores
changed since the latest saved file. Nothing is ever inferred: a game without a final score from its
source stays AWAITING_RESULT. A later file that changes a game's score is reported as a correction;
both versions stay on record and the latest is used.
"""

import argparse
import csv
import datetime
import glob
import gzip
import io
import json
import os
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

from sportsbook_v4.util import json_hash

FORMAT = "v4_results_1"
NFLVERSE_GAMES = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
ET = ZoneInfo("America/New_York")

NFL_ABBR = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers", "CHI": "Chicago Bears", "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys", "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars", "KC": "Kansas City Chiefs",
    "LV": "Las Vegas Raiders", "LAC": "Los Angeles Chargers", "LA": "Los Angeles Rams", "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings", "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers", "SF": "San Francisco 49ers",
    "SEA": "Seattle Seahawks", "TB": "Tampa Bay Buccaneers", "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def season_for(t):
    g = time.gmtime(t)
    return g.tm_year if g.tm_mon >= 3 else g.tm_year - 1


def parse_nflverse(text, season):
    """Final games of a season: rows with both scores present."""
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        if r["season"] != str(season) or r["home_score"] in ("", "NA") or r["away_score"] in ("", "NA"):
            continue
        home, away = NFL_ABBR.get(r["home_team"]), NFL_ABBR.get(r["away_team"])
        if not home or not away:
            continue
        out.append({"source_id": r["game_id"], "home": home, "away": away, "date_et": r["gameday"],
                    "home_score": int(r["home_score"]), "away_score": int(r["away_score"]), "final": True})
    return sorted(out, key=lambda x: x["source_id"])


def parse_cfbd(games):
    out = []
    for g in games or []:
        done = g.get("completed")
        hp = g.get("homePoints", g.get("home_points"))
        ap = g.get("awayPoints", g.get("away_points"))
        if not done or hp is None or ap is None:
            continue
        start = g.get("startDate") or g.get("start_date")
        out.append({"source_id": str(g.get("id")), "home": g.get("homeTeam") or g.get("home_team"),
                    "away": g.get("awayTeam") or g.get("away_team"), "start": start,
                    "home_score": int(hp), "away_score": int(ap), "final": True})
    return sorted(out, key=lambda x: x["source_id"])


def _get(url, headers=None, opener=None):
    if opener:
        return opener(url)
    req = urllib.request.Request(url, headers={"User-Agent": "arbitredge-v4", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def fetch(sport, now, key=None, opener=None):
    season = season_for(now)
    if sport == "nfl":
        rows = parse_nflverse(_get(NFLVERSE_GAMES, opener=opener).decode(), season)
        src = {"source": "nflverse games.csv", "url": NFLVERSE_GAMES}
    else:
        from data_v4.rankings import CFBD_BASE
        types = ["regular"] + (["postseason"] if time.gmtime(now).tm_mon in (12, 1, 2) else [])
        games = []
        for st in types:   # 1 CFBD call per season type (free tier)
            games += json.loads(_get(f"{CFBD_BASE}/games?year={season}&seasonType={st}",
                                     {"Authorization": f"Bearer {key}", "Accept": "application/json"}, opener))
        rows = parse_cfbd(games)
        src = {"source": "CollegeFootballData /games", "url": f"{CFBD_BASE}/games?year={season}"}
    return {"format": FORMAT, "sport": sport, "season": season, "fetched_at": _iso(now), **src,
            "rows": rows, "rows_hash": json_hash(rows)}


def load(directory):
    """All saved results files, hash-verified, oldest first."""
    docs = []
    for p in sorted(glob.glob(os.path.join(directory, "**", "results_*.json.gz"), recursive=True)) if directory else []:
        with gzip.open(p, "rt") as f:
            d = json.load(f)
        if json_hash(d["rows"]) != d["rows_hash"]:
            raise ValueError(f"{os.path.basename(p)}: results hash mismatch")
        docs.append(d)
    return sorted(docs, key=lambda d: (d["sport"], d["fetched_at"]))


def save_if_changed(doc, existing_docs, out_dir):
    prev = [d for d in existing_docs if d["sport"] == doc["sport"] and d["season"] == doc["season"]]
    if prev and prev[-1]["rows_hash"] == doc["rows_hash"]:
        return None
    os.makedirs(out_dir, exist_ok=True)
    name = f"results_{doc['sport']}_{doc['fetched_at'].replace('-', '').replace(':', '')}.json.gz"
    path = os.path.join(out_dir, name)
    if os.path.exists(path):
        raise FileExistsError(name)
    with gzip.open(path, "wt") as f:
        json.dump(doc, f, sort_keys=True)
    return path


# ------------------------------------------------------------------ matching

def _et_date(epoch):
    return datetime.datetime.fromtimestamp(epoch, ET).date().isoformat()


def match(games, docs, cfb_matcher=None):
    """{(sport, event_id): {home_score, away_score, source_id, source, fetched_at, corrected}} for every game
    (Odds API names + kickoff) whose final score is found. Ambiguous or unmatched games are left out."""
    from sportsbook_v4.util import to_epoch
    latest, history = {}, {}
    for d in docs:              # oldest first: later fetches override, earlier differing finals are kept
        for r in d["rows"]:
            k = (d["sport"], r["source_id"])
            history.setdefault(k, set()).add((r["home_score"], r["away_score"]))
            latest[k] = dict(r, source=d["source"], fetched_at=d["fetched_at"])
    by_sport = {}
    for (sp, _sid), r in latest.items():
        by_sport.setdefault(sp, []).append(r)
    out = {}
    for g in games:
        kick = to_epoch(g["kickoff"])
        cands = []
        for r in by_sport.get(g["sport"], []):
            if g["sport"] == "nfl":
                ok = r["home"] == g["home"] and r["away"] == g["away"] and r["date_et"] == _et_date(kick)
            else:
                if cfb_matcher is None:
                    continue
                ok = (cfb_matcher.school_for(g["home"]) == r["home"] and cfb_matcher.school_for(g["away"]) == r["away"]
                      and r.get("start") and abs(to_epoch(r["start"]) - kick) <= 36 * 3600)
            if ok:
                cands.append(r)
        if len(cands) == 1:
            r = cands[0]
            out[(g["sport"], g["event_id"])] = {
                "home_score": r["home_score"], "away_score": r["away_score"], "source_id": r["source_id"],
                "source": r["source"], "fetched_at": r["fetched_at"],
                "corrected": len(history[(g["sport"], r["source_id"])]) > 1}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", choices=("nfl", "cfb"), required=True)
    ap.add_argument("--existing", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    key = None
    if a.sport == "cfb":
        key = os.environ.get("CFBD_API_KEY", "").strip()
        if not key:
            print("::error::CFBD_API_KEY secret is not available to this job")
            return 1
    now = time.time()
    try:
        doc = fetch(a.sport, now, key)
    except Exception as e:  # never echo the key
        msg = str(e).replace(key, "[REDACTED]") if key else str(e)
        print(f"::error::{a.sport} results fetch failed: {msg}")
        return 1
    path = save_if_changed(doc, load(a.existing), a.out)
    print(json.dumps({"sport": a.sport, "season": doc["season"], "final_games": len(doc["rows"]),
                      "saved": os.path.basename(path) if path else None}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
