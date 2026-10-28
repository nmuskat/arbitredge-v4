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
  * Poll week: the AP poll is released on Sundays. If a new weekly poll is
    due (NEXT_RELEASE_WEEKDAY/HOUR_UTC) between the fetch and the kickoff,
    the poll that will apply to that game does not exist yet: status
    PENDING, with the current ranks attached only as `provisional`.
    Exception: bowl season (POSTSEASON_FROM onward), when no weekly poll
    follows the final regular-season poll; it then applies for up to
    POSTSEASON_MAX_AGE_DAYS.
  * Team names are matched to CFBD schools with the FULL FBS team list,
    comparing names with accents and punctuation removed: "<name> <mascot>"
    for the school AND each CFBD alternate name ("Southern Mississippi Golden
    Eagles" -> Southern Miss, "San Jose State Spartans" -> San José State),
    then the LONGEST official school name that prefixes the provider name (so
    "Texas A&M Aggies" never matches "Texas", nor "Miami (OH) RedHawks"
    match "Miami"). A name that fits two schools is treated as unmatched.
    Unmatched names are reported, and an unmatched game is never called
    unranked.
"""

import calendar
import json
import re
import time
import unicodedata
import urllib.request

CFBD_BASE = "https://api.collegefootballdata.com"
AP_POLL = "AP Top 25"
MAX_POLL_AGE_DAYS = 10
NEXT_RELEASE_WEEKDAY = 6          # Sunday (time.gmtime: Monday = 0)
NEXT_RELEASE_HOUR_UTC = 17        # AP releases ~2 pm ET; 17:00 UTC is the conservative (earlier) bound
POSTSEASON_FROM = (12, 8)         # (month, day): bowl season, no further weekly poll
POSTSEASON_MAX_AGE_DAYS = 45


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


def norm(name):
    """Lower case, accents removed, punctuation dropped, whitespace collapsed."""
    n = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    n = re.sub(r"[.'\u2019]", "", n.lower())
    return " ".join(re.sub(r"[^a-z0-9&()-]+", " ", n).split())


class TeamMatcher:
    def __init__(self, teams):
        full, self.schools = {}, []
        for t in teams:
            school = t["school"]
            self.schools.append(school)
            names = {school, *(t.get("alternate_names") or [])}
            for nm in names:
                keys = {norm(nm)}
                if t.get("mascot"):
                    keys.add(norm(f"{nm} {t['mascot']}"))
                for k in keys:
                    full.setdefault(k, set()).add(school)
        self.full = {k: next(iter(v)) for k, v in full.items() if len(v) == 1}      # ambiguous -> dropped
        self.schools.sort(key=lambda x: len(norm(x)), reverse=True)

    def school_for(self, provider_name):
        n = norm(provider_name)
        if n in self.full:
            return self.full[n]
        for s in self.schools:                     # longest official school name first
            k = norm(s)
            if n == k or n.startswith(k + " "):
                return s
        return None


def next_release(after):
    """First weekly AP release time strictly after `after` (epoch)."""
    g = time.gmtime(after)
    day0 = calendar.timegm((g.tm_year, g.tm_mon, g.tm_mday, 0, 0, 0))
    t = day0 + ((NEXT_RELEASE_WEEKDAY - g.tm_wday) % 7) * 86400 + NEXT_RELEASE_HOUR_UTC * 3600
    return t if t > after else t + 7 * 86400


def _postseason(kickoff):
    g = time.gmtime(kickoff)
    return g.tm_mon in (1, 2) or (g.tm_mon, g.tm_mday) >= POSTSEASON_FROM    # bowls through the off-season


def applicable_poll(snapshots, kickoff):
    """(snapshot, poll) for the latest AP poll fetched at or before kickoff and not older than
    MAX_POLL_AGE_DAYS; (None, reason) otherwise."""
    usable = [s for s in snapshots if s["fetched_at"] <= kickoff and s.get("polls")]
    if not usable:
        return None, "no AP poll had been fetched before kickoff"
    snap = max(usable, key=lambda s: s["fetched_at"])
    order = {"regular": 0, "postseason": 1}
    poll = max(snap["polls"], key=lambda p: (order.get(p.get("season_type"), 0), p.get("week") or 0))
    post = _postseason(kickoff)
    if not post and next_release(snap["fetched_at"]) <= kickoff:
        nr = time.strftime("%a %Y-%m-%d", time.gmtime(next_release(snap["fetched_at"])))
        return ("PENDING", snap, poll), (f"the AP poll for this game's week (released from {nr}) has not been "
                                         f"fetched yet; latest fetched is week {poll.get('week')}")
    max_age = POSTSEASON_MAX_AGE_DAYS if post else MAX_POLL_AGE_DAYS
    if kickoff - snap["fetched_at"] > max_age * 86400:
        return None, f"latest AP poll fetch is more than {max_age} days before kickoff"
    return (snap, poll), None


def top25_status(home, away, kickoff, snapshots):
    found, why = applicable_poll(snapshots, kickoff)
    if not found:
        return {"status": "UNAVAILABLE", "reason": why}
    if found[0] == "PENDING":
        _, snap, poll = found
        m = TeamMatcher(snap.get("teams") or [])
        rank = {r["school"]: r["rank"] for r in poll["ranks"]}
        return {"status": "PENDING", "reason": why, "top25": None, "source": snap["source"],
                "fetched_at": snap["fetched_at"],
                "provisional": {"poll_week": poll.get("week"), "home_rank": rank.get(m.school_for(home)),
                                "away_rank": rank.get(m.school_for(away)),
                                "note": "current ranks, NOT the poll that will apply at kickoff"}}
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
