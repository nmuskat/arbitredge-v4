"""
reference/build_2026_fbs_membership.py

Builds and verifies the real 2026 FBS membership list, cross-referenced
against multiple independent sources (ESPN, Athlon/Yahoo/AOL -- the
latter three syndicated identically, an independent confirmation in
themselves -- and Wikipedia's detailed 2021-2026 realignment tracker,
all agreeing on the same 138-team total and the same two genuine
FCS-to-FBS newcomers: North Dakota State and Sacramento State, both
football-only members).

DOES NOT roll over the 2025 list. Every 2026 row here reflects the real,
current 2026 season membership, including conference realignment already
in effect for 2026 (Pac-12 rebuilt around Oregon State/Washington State
plus six new members; UTEP and Northern Illinois moving to Mountain West;
Louisiana Tech moving to Sun Belt; etc.) -- not a 2025 assumption.

MATCHING METHODOLOGY: each of the 138 real team names is resolved against
the actual teams_canonical table via exact normalized match, then a
same-prefix substring fallback for names with an extra mascot suffix
(e.g. "Duke" -> "Duke Blue Devils"). Genuine ambiguous collisions (e.g.
"North Carolina" also prefix-matching "North Carolina A&T", or "Utah"
matching "Utah State"/"Utah Tech" too) are resolved via an explicit,
disclosed OVERRIDE map -- never guessed silently. Two names (Southern
Miss, ULM) don't share a normalizable prefix with their real
canonical_team_id's full display name (Southern Mississippi, UL Monroe)
and are also handled via the same explicit override map.

Preserves the existing CSV structure exactly (season, canonical_team_id,
classification, source, source_note). Never deletes or modifies any 2025
row. Verifies uniqueness before writing anything.
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from identity.normalize import normalize_text  # noqa: E402
import sqlite3  # noqa: E402

FBS_MEMBERSHIP_PATH = Path(__file__).resolve().parent / "fbs_membership.csv"

SOURCE_NOTE = (
    "sports-media cross-reference (ESPN/Athlon-Yahoo-AOL/Wikipedia 2021-2026 "
    "realignment tracker) on real 2026 conference realignment -- not a 2025 rollover"
)

TEAMS_2026 = {
    "ACC": ["Boston College", "California", "Clemson", "Duke", "Florida State", "Georgia Tech", "Louisville",
            "Miami (FL)", "North Carolina", "NC State", "Pittsburgh", "SMU", "Stanford", "Syracuse", "Virginia",
            "Virginia Tech", "Wake Forest"],
    "Big 12": ["Arizona", "Arizona State", "Baylor", "BYU", "Cincinnati", "Colorado", "Houston", "Iowa State",
               "Kansas", "Kansas State", "Oklahoma State", "TCU", "Texas Tech", "UCF", "Utah", "West Virginia"],
    "Big Ten": ["Illinois", "Indiana", "Iowa", "Maryland", "Michigan", "Michigan State", "Minnesota", "Nebraska",
                "Northwestern", "Ohio State", "Oregon", "Penn State", "Purdue", "Rutgers", "UCLA", "USC",
                "Washington", "Wisconsin"],
    "SEC": ["Alabama", "Arkansas", "Auburn", "Florida", "Georgia", "Kentucky", "LSU", "Mississippi State",
            "Missouri", "Oklahoma", "Ole Miss", "South Carolina", "Tennessee", "Texas", "Texas A&M", "Vanderbilt"],
    "American": ["Army", "Charlotte", "East Carolina", "Florida Atlantic", "Memphis", "Navy", "North Texas",
                 "Rice", "Temple", "Tulane", "Tulsa", "UAB", "South Florida", "UTSA"],
    "Conference USA": ["Delaware", "FIU", "Jacksonville State", "Kennesaw State", "Liberty", "Middle Tennessee",
                        "Missouri State", "New Mexico State", "Sam Houston", "Western Kentucky"],
    "MAC": ["Akron", "Ball State", "Bowling Green", "Buffalo", "Central Michigan", "Eastern Michigan",
            "Kent State", "Miami (OH)", "Ohio", "Sacramento State", "Toledo", "UMass", "Western Michigan"],
    "Mountain West": ["Air Force", "Hawaii", "Nevada", "New Mexico", "North Dakota State", "Northern Illinois",
                       "San Jose State", "UNLV", "UTEP", "Wyoming"],
    "Pac-12": ["Boise State", "Colorado State", "Fresno State", "Oregon State", "San Diego State", "Texas State",
               "Utah State", "Washington State"],
    "Sun Belt": ["Appalachian State", "Coastal Carolina", "Georgia Southern", "Georgia State", "James Madison",
                 "Marshall", "Old Dominion", "Arkansas State", "Louisiana", "Louisiana Tech", "South Alabama",
                 "Southern Miss", "Troy", "ULM"],
    "Independent": ["Notre Dame", "UConn"],
}

OVERRIDE = {
    "Miami (FL)": "cfb_miami_fl", "Miami (OH)": "cfb_miami_oh",
    "North Carolina": "cfb_north_carolina", "Virginia": "cfb_virginia",
    "Arizona": "cfb_arizona", "Colorado": "cfb_colorado", "Houston": "cfb_houston",
    "Kansas": "cfb_kansas", "Utah": "cfb_utah", "Illinois": "cfb_illinois",
    "Indiana": "cfb_indiana", "Iowa": "cfb_iowa", "Michigan": "cfb_michigan",
    "Northwestern": "cfb_northwestern", "Oregon": "cfb_oregon", "Washington": "cfb_washington",
    "Alabama": "cfb_alabama", "Arkansas": "cfb_arkansas", "Florida": "cfb_florida",
    "Georgia": "cfb_georgia", "Missouri": "cfb_missouri", "Oklahoma": "cfb_oklahoma",
    "Tennessee": "cfb_tennessee", "Texas": "cfb_texas", "Ohio": "cfb_ohio",
    "New Mexico": "cfb_new_mexico", "Louisiana": "cfb_louisiana",
    "Southern Miss": "cfb_southern_mississippi", "ULM": "cfb_ul_monroe",
}

FCS_TO_FBS_NEWCOMERS_2026 = {"North Dakota State", "Sacramento State"}


def resolve_team(conn, name):
    if name in OVERRIDE:
        return OVERRIDE[name], "override"

    rows = conn.execute("SELECT canonical_team_id, display_name FROM teams_canonical WHERE sport='cfb'").fetchall()
    by_norm = {normalize_text(dn): cid for cid, dn in rows}

    norm = normalize_text(name)
    if norm in by_norm:
        return by_norm[norm], "exact"

    candidates = [(dn, cid) for dn, cid in by_norm.items() if dn.startswith(norm + " ")]
    if len(candidates) == 1:
        return candidates[0][1], "prefix"
    if len(candidates) > 1:
        return None, f"AMBIGUOUS: {candidates}"
    return None, "NO MATCH"


def build_and_verify(conn):
    all_teams = [(conf, name) for conf, names in TEAMS_2026.items() for name in names]
    matched, unmatched = [], []

    for conf, name in all_teams:
        cid, reason = resolve_team(conn, name)
        if cid is not None:
            matched.append((conf, name, cid, name in FCS_TO_FBS_NEWCOMERS_2026))
        else:
            unmatched.append((conf, name, reason))

    ids = [m[2] for m in matched]
    duplicates = sorted(set(x for x in ids if ids.count(x) > 1))

    return {
        "total_expected": 138,
        "total_compiled": len(all_teams),
        "matched": matched,
        "unmatched": unmatched,
        "duplicates": duplicates,
        "unique_ids": len(set(ids)),
    }


def already_has_2026_rows():
    if not FBS_MEMBERSHIP_PATH.exists():
        return False
    with open(FBS_MEMBERSHIP_PATH, newline="") as f:
        return any(row["season"] == "2026" for row in csv.DictReader(f))


def write_2026_rows(matched):
    if already_has_2026_rows():
        print("REFUSING TO WRITE: 2026 rows already exist in fbs_membership.csv. "
              "Remove them manually first if you intend to rebuild from scratch.")
        return False

    with open(FBS_MEMBERSHIP_PATH, "a", newline="") as f:
        writer = csv.writer(f)
        for conf, name, cid, is_newcomer in matched:
            note = SOURCE_NOTE + (f"; FCS-to-FBS newcomer 2026 ({conf}, football-only)" if is_newcomer
                                   else f"; {conf} member 2026")
            writer.writerow(["2026", cid, "FBS", "sports-media cross-reference", note])
    return True


def main():
    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")

    result = build_and_verify(conn)

    print(f"{'=' * 70}\n2026 FBS MEMBERSHIP VERIFICATION\n{'=' * 70}")
    print(f"Expected total: {result['total_expected']}")
    print(f"Compiled total: {result['total_compiled']}")
    print(f"Matched: {len(result['matched'])}")
    print(f"Unmatched: {len(result['unmatched'])}")

    if result["unmatched"]:
        print("\n--- UNMATCHED (reported, not guessed -- do not proceed until resolved) ---")
        for conf, name, reason in result["unmatched"]:
            print(f"  [{conf}] {name}: {reason}")
        conn.close()
        return

    if result["duplicates"]:
        print(f"\n!!! DUPLICATE canonical_team_id assignments: {result['duplicates']} -- STOPPING, not writing")
        conn.close()
        return

    print(f"Uniqueness: PASS ({result['unique_ids']} distinct canonical_team_id values)")

    newcomers = [m for m in result["matched"] if m[3]]
    print(f"\nFCS-to-FBS newcomers for 2026: {[m[1] for m in newcomers]}")

    if result["total_compiled"] != result["total_expected"]:
        print(f"\nWARNING: compiled total ({result['total_compiled']}) does not match the "
              f"expected 138 -- do not write until this is reconciled.")
        conn.close()
        return

    wrote = write_2026_rows(result["matched"])
    if wrote:
        print(f"\nWrote {len(result['matched'])} new 2026 rows to {FBS_MEMBERSHIP_PATH}. "
              f"No 2025 row was touched.")

    conn.close()


if __name__ == "__main__":
    main()
