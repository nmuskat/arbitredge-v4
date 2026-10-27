"""
data_v4/rankings.py -- AP Top-25 for college football: source, provenance,
team matching and "which poll applies to this game".

Source: CollegeFootballData API (free tier, API key via the GitHub secret
CFBD_API_KEY; fetched by .github/workflows/cfb-rankings.yml, never by the
website). Field names follow CFBD's published schema and are parsed
defensively; every snapshot records source, URL and fetch time.

Rules
  * A game uses the most recent AP poll FETCHED BEFORE ITS KICKOFF, and only
    if that fetch is at most MAX_POLL_AGE_DAYS old. Otherwise the game's
    Top-25 status is UNAVAILABLE -- never assumed ranked or unranked.
  * Team names are matched to CFBD schools with the FULL FBS team list:
    exact "school mascot" first, then the LONGEST school name that prefixes
    the provider name (so "Texas A&M Aggies" never matches "Texas", nor
    "Miami (OH) RedHawks" match "Miami"). Unmatched names are reported.
"""

import json
import time
import urllib.request

CFBD_BASE = "https://api.collegefootballdata.com"
AP_POLL = "AP Top 25"
MAX_POLL_AGE_DAYS = 10


def _get(path, key, opener=None):
    url = CFBD_BASE + path
    if opener:
        return opener(url)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                                               "User-Agent": "arbitredge-v4"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def fetch(year, key, now=None, opener=None):
    """One snapshot: AP polls (regular + postseason) and the FBS team list. 3 CFBD calls."""
    now = now or time.time()
    snap = {"source": "CollegeFootballData API", "fetched_at": now, "year": year, "polls": [], "teams": []}
    for season_type in ("regular", "postseason"):
        data = _get(f"/rankings?year={year}&seasonType={season_type}", key, opener) or []
        snap["polls"] += parse_ap(data)
    snap["teams"] = [{"school": t.get("school"), "mascot": t.get("mascot"),
                      "alternate_names": t.get("alternateNames") or t.get("alternate_names") or []}
                     for t in (_get(f"/teams/fbs?year={year}", key, opener) or []) if t.get("school")]
    return snap


def parse_ap(payload):
    out = []
    for wk in payload or []:
        for poll in wk.get("polls") or []:
            if (poll.get("poll") or "").strip().lower() != AP_POLL.lower():
                continue
            ranks = sorted(({"rank": int(r["rank"]), "school": r.get("school") or r.get("team")}
                            for r in poll.get("ranks") or [] if r.get("rank") is not None and (r.get("school") or r.get("team"))),
                           key=lambda r: r["rank"])
            out.append({"season": wk.get("season"), "season_type": wk.get("seasonType") or wk.get("season_type"),
                        "week": wk.get("week"), "ranks": ranks})
    return out


class TeamMatcher:
    def __init__(self, teams):
        self.full = {}
        self.schools = []
        for t in teams:
            school = t["school"]
            self.schools.append(school)
            if t.get("mascot"):
                self.full[f"{school} {t['mascot']}".lower()] = school
            for alt in t.get("alternate_names") or []:
                self.full.setdefault(str(alt).lower(), school)
        self.schools.sort(key=len, reverse=True)

    def school_for(self, provider_name):
        n = (provider_name or "").strip()
        if n.lower() in self.full:
            return self.full[n.lower()]
        for s in self.schools:                     # longest school name first
            if n == s or n.startswith(s + " "):
                return s
        return None


def applicable_poll(snapshots, kickoff):
    """(snapshot, poll) for the latest AP poll fetched at or before kickoff and not older than
    MAX_POLL_AGE_DAYS; (None, reason) otherwise."""
    usable = [s for s in snapshots if s["fetched_at"] <= kickoff and s.get("polls")]
    if not usable:
        return None, "no AP poll had been fetched before kickoff"
    snap = max(usable, key=lambda s: s["fetched_at"])
    if kickoff - snap["fetched_at"] > MAX_POLL_AGE_DAYS * 86400:
        return None, f"latest AP poll fetch is older than {MAX_POLL_AGE_DAYS} days"
    order = {"regular": 0, "postseason": 1}
    poll = max(snap["polls"], key=lambda p: (order.get(p.get("season_type"), 0), p.get("week") or 0))
    return (snap, poll), None


def top25_status(home, away, kickoff, snapshots):
    found, why = applicable_poll(snapshots, kickoff)
    if not found:
        return {"status": "UNAVAILABLE", "reason": why}
    snap, poll = found
    m = TeamMatcher(snap.get("teams") or [])
    rank = {r["school"]: r["rank"] for r in poll["ranks"]}
    hs, as_ = m.school_for(home), m.school_for(away)
    unmatched = [n for n, s in ((home, hs), (away, as_)) if s is None]
    out = {"status": "OK", "home_rank": rank.get(hs), "away_rank": rank.get(as_),
           "poll": AP_POLL, "poll_week": poll.get("week"), "season_type": poll.get("season_type"),
           "source": snap["source"], "fetched_at": snap["fetched_at"], "unmatched": unmatched}
    out["top25"] = out["home_rank"] is not None or out["away_rank"] is not None
    if unmatched and not out["top25"]:
        # an unmatched team could be a ranked one under another name: do not call it unranked
        out.update(status="UNAVAILABLE", reason=f"team name not matched to CFBD: {', '.join(unmatched)}")
    return out
