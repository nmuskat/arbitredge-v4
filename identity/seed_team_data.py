"""
identity/seed_team_data.py

Loads identity/seed_data/*.csv into teams_canonical and team_aliases.
Idempotent: safe to re-run. Comment lines (starting with '#') and blank
lines in the CSVs are skipped.

Before inserting, runs a conflict check: if a proposed alias's normalized
text would match another alias already mapped to a *different* canonical
team (within the same sport), the load is aborted with a clear error
listing every conflict, rather than silently seeding an ambiguous alias.
This is a seed-time safety net on top of team_resolver.py's own runtime
ambiguity handling.

Usage:
    python identity/seed_team_data.py [--db-path PATH]
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from identity.normalize import normalize_text  # noqa: E402
from db.migrate import DEFAULT_DB_PATH, run_migration  # noqa: E402

SEED_DIR = Path(__file__).parent / "seed_data"

TEAM_FILES = ["nfl_teams.csv", "cfb_teams.csv"]
ALIAS_FILES = ["nfl_aliases.csv", "cfb_aliases.csv"]


def _read_csv_rows(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            first_val = next(iter(row.values()), "")
            if first_val is None or first_val.strip() == "" or first_val.strip().startswith("#"):
                continue
            yield row


def load_teams(conn: sqlite3.Connection) -> int:
    count = 0
    for filename in TEAM_FILES:
        path = SEED_DIR / filename
        for row in _read_csv_rows(path):
            conn.execute(
                "INSERT OR IGNORE INTO teams_canonical (canonical_team_id, sport, display_name) "
                "VALUES (?, ?, ?)",
                (row["canonical_team_id"], row["sport"], row["display_name"]),
            )
            count += 1
    conn.commit()
    return count


def check_alias_conflicts(conn: sqlite3.Connection) -> list[str]:
    """
    Returns a list of human-readable conflict descriptions. Empty list
    means no conflicts found. A conflict is: the same (sport,
    normalized_text) mapping to more than one distinct canonical_team_id.
    """
    rows = conn.execute(
        "SELECT ta.alias_text, ta.source, tc.canonical_team_id, tc.sport "
        "FROM team_aliases ta JOIN teams_canonical tc "
        "ON ta.canonical_team_id = tc.canonical_team_id"
    ).fetchall()

    normalized_map: dict[tuple, set] = {}
    origin_map: dict[tuple, list] = {}
    for alias_text, source, canonical_team_id, sport in rows:
        norm = normalize_text(alias_text)
        key = (sport, norm)
        normalized_map.setdefault(key, set()).add(canonical_team_id)
        origin_map.setdefault(key, []).append((alias_text, source, canonical_team_id))

    conflicts = []
    for key, team_ids in normalized_map.items():
        if len(team_ids) > 1:
            sport, norm = key
            details = "; ".join(
                f"'{a}' (source={s}) -> {c}" for a, s, c in origin_map[key]
            )
            conflicts.append(
                f"CONFLICT [{sport}] normalized text '{norm}' maps to {len(team_ids)} "
                f"different teams: {details}"
            )
    return conflicts


def load_aliases(conn: sqlite3.Connection) -> int:
    count = 0
    for filename in ALIAS_FILES:
        path = SEED_DIR / filename
        for row in _read_csv_rows(path):
            conn.execute(
                "INSERT OR IGNORE INTO team_aliases (canonical_team_id, source, alias_text) "
                "VALUES (?, ?, ?)",
                (row["canonical_team_id"], row["source"], row["alias_text"]),
            )
            count += 1
    conn.commit()
    return count


def seed(db_path: Path) -> None:
    run_migration(db_path)  # ensure schema exists first
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        team_count = load_teams(conn)
        alias_count = load_aliases(conn)

        conflicts = check_alias_conflicts(conn)
        if conflicts:
            print("ALIAS SEED CONFLICTS DETECTED -- refusing to consider seed clean:")
            for c in conflicts:
                print(f"  {c}")
            raise SystemExit(
                f"\n{len(conflicts)} conflict(s) found. Fix seed_data CSVs before proceeding."
            )

        print(f"Seeded {team_count} team rows and {alias_count} alias rows (idempotent).")
        print("No alias conflicts detected.")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed team/alias identity data.")
    parser.add_argument("--db-path", type=str, default=str(DEFAULT_DB_PATH))
    args = parser.parse_args()
    seed(Path(args.db_path))
