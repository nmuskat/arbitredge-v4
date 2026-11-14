"""
splits_v4/match.py -- match each site's game to our odds feed's game (Odds API names and kickoffs).

Kickoff within 45 min (when the site gives a time) AND both team names must match: full name, school/city prefix,
nickname, or abbreviation (letters in order, first letters agreeing). WagerTalk rows are joined to games through
SportsBettingDime's rotation numbers first. Ambiguous or unmatched games are left out and counted, never guessed.
"""
import re

KO_TOL_S = 45 * 60
NFL_ABBR = {"ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
            "CAR": "Carolina Panthers", "CHI": "Chicago Bears", "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
            "DAL": "Dallas Cowboys", "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
            "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars", "JAC": "Jacksonville Jaguars",
            "KC": "Kansas City Chiefs", "LV": "Las Vegas Raiders", "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams",
            "LA": "Los Angeles Rams", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings", "NE": "New England Patriots",
            "NO": "New Orleans Saints", "NYG": "New York Giants", "NYJ": "New York Jets", "PHI": "Philadelphia Eagles",
            "PIT": "Pittsburgh Steelers", "SF": "San Francisco 49ers", "SEA": "Seattle Seahawks", "TB": "Tampa Bay Buccaneers",
            "TEN": "Tennessee Titans", "WAS": "Washington Commanders", "WSH": "Washington Commanders"}


# school names that differ between sites and the odds feed (normalised forms; extend when a game goes unmatched)
ALIASES = {"louisiana monroe": "ul monroe", "ulm": "ul monroe", "louisiana lafayette": "louisiana", "ull": "louisiana",
           "miami oh": "miami ohio", "miami fl": "miami", "southern miss": "southern mississippi", "uconn": "connecticut",
           "conn": "connecticut", "umass": "massachusetts", "fiu": "florida international", "fau": "florida atlantic",
           "utsa": "ut san antonio", "unlv": "unlv", "byu": "byu", "smu": "smu", "tcu": "tcu", "lsu": "lsu", "ucf": "ucf",
           "usc": "usc", "ucla": "ucla", "app state": "appalachian state", "ole miss": "ole miss", "pitt": "pittsburgh",
           "usf": "south florida", "ku": "kansas", "ksu": "kansas state", "uga": "georgia", "bama": "alabama",
           "utep": "utep", "uab": "uab", "nd": "notre dame", "psu": "penn state", "fsu": "florida state",
           # official names (BetMGM's college page)
           "southern methodist": "smu", "north carolina state": "nc state", "louisiana state": "lsu",
           "texas christian": "tcu", "brigham young": "byu", "central florida": "ucf", "southern california": "usc",
           "pennsylvania state": "penn state", "miami university": "miami ohio", "virginia polytechnic institute and state": "virginia tech",
           "georgia institute of technology": "georgia tech", "texas a and m": "texas a and m", "mississippi": "ole miss",
           "the ohio state": "ohio state", "nevada las vegas": "unlv", "alabama at birmingham": "uab",
           "texas at el paso": "utep", "texas at san antonio": "ut san antonio", "massachusetts amherst": "massachusetts",
           "georgia instute of technlogy": "georgia tech", "north carolina at charlotte": "charlotte",
           "north carolina at chapel hill": "north carolina", "indiana university bloomington": "indiana",
           "indiana bloomington": "indiana", "united states air force academy": "air force",
           "united states military academy": "army", "united states naval academy": "navy",
           "middle tennessee state": "middle tennessee", "university at buffalo": "buffalo"}


def official(name):
    """'University of Colorado, Boulder' -> 'Colorado'; 'University of California, Los Angeles (UCLA)' -> 'UCLA';
    'Southern Methodist University' -> 'Southern Methodist'. Used for sites that print official school names."""
    m = re.search(r"\(([^)]+)\)", name)
    if m:
        return m.group(1).strip()
    n = name.split(",")[0].strip()
    n = re.sub(r"^(the\s+)?university of\s+", "", n, flags=re.I)
    n = re.sub(r"\s+(university|college|state university)$", lambda x: " state" if "state" in x.group(0).lower() else "", n, flags=re.I)
    return n.strip()


def _norm(s):
    import unicodedata
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))   # San José -> San Jose
    s = s.lower().replace("&", " and ").replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    for k, v in ALIASES.items():
        if s == k or s.startswith(k + " "):
            return v + s[len(k):]
    return s


def name_score(src, full, sport):
    """0 (no match) .. 3 (identical)."""
    if not src or not full:
        return 0.0
    if sport == "nfl" and src.upper() in NFL_ABBR:
        return 3.0 if NFL_ABBR[src.upper()] == full else 0.0
    a, b = _norm(src), _norm(full)
    if a == b:
        return 3.0
    if b.startswith(a + " "):
        return 2.5
    ta, tb = a.split(), b.split()
    if sport == "nfl" and ta and ta[-1] == tb[-1]:
        return 2.0
    if sport == "cfb" and len(ta) >= 2 and len(tb) >= 2 and ta[-1] == tb[-1] and len(ta[-1]) > 3:
        return 1.2                                    # same mascot (kickoff already agrees)
    if set(ta) <= set(tb) and len(ta) >= 1 and len(a) > 3:
        return 1.8
    if re.fullmatch(r"[a-z]{2,5}", a):               # abbreviation: USF, ULM, MEM, Wvu, Ku
        initials = "".join(t[0] for t in tb)
        if initials.startswith(a) or a == initials[:len(a)]:
            return 1.5
        it = iter(b.replace(" ", ""))
        if a[0] == b[0] and all(ch in it for ch in a):
            return 1.0
    return 0.0


def match_rows(rows, events, rot_map=None):
    """rows: one site's parsed rows; events: [{'event_id','sport','kickoff','home','away'}].
    Returns ({event_id: row}, unmatched_count)."""
    out, unmatched = {}, 0
    for r in rows:
        cands = [e for e in events if e["sport"] == r["sport"]
                 and (r.get("kickoff") is None or abs(e["kickoff"] - r["kickoff"]) <= KO_TOL_S)]
        if rot_map and r.get("rot") and tuple(r["rot"]) in rot_map:
            eid = rot_map[tuple(r["rot"])]
            if any(e["event_id"] == eid for e in cands):
                out.setdefault(eid, r)
                continue
        scored = []
        for e in cands:
            sa, sh = name_score(r["away"], e["away"], e["sport"]), name_score(r["home"], e["home"], e["sport"])
            if sa > 0 and sh > 0:
                scored.append((sa + sh, e["event_id"]))
        scored.sort(reverse=True)
        if not scored or (len(scored) > 1 and scored[0][0] == scored[1][0]):
            unmatched += 1
            continue
        out.setdefault(scored[0][1], r)
    return out, unmatched
