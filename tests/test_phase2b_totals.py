import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulation.offered_line import construct_offered_total


def fresh_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE games_canonical (
            canonical_game_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sport TEXT NOT NULL,
            home_team_id TEXT NOT NULL,
            away_team_id TEXT NOT NULL,
            commence_time TEXT NOT NULL
        );
        CREATE TABLE market_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_game_id INTEGER NOT NULL,
            sport TEXT NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            commence_time TEXT NOT NULL,
            book TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            book_last_update TEXT,
            spread_home REAL,
            spread_away REAL,
            spread_price_home REAL,
            spread_price_away REAL,
            total_points REAL,
            total_price_over REAL,
            total_price_under REAL
        );
        """
    )
    conn.commit()
    return conn


def seed_game(conn, rows):
    cur = conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) "
        "VALUES ('nfl', 'kc', 'lac', '2026-09-10T20:00:00Z')",
    )
    gid = cur.lastrowid
    for book, observed_at, total, over_price, under_price in rows:
        conn.execute(
            """
            INSERT INTO market_snapshots (
                canonical_game_id, sport, home_team, away_team, commence_time,
                book, observed_at, total_points, total_price_over, total_price_under
            ) VALUES (?,'nfl','kc','lac','2026-09-10T20:00:00Z',?,?,?,?,?)
            """,
            (gid, book, observed_at, total, over_price, under_price),
        )
    conn.commit()
    return gid


class TestConstructOfferedTotal(unittest.TestCase):
    def test_lower_median_representative_total(self):
        conn = fresh_conn()
        gid = seed_game(conn, [
            ("booka", "2026-09-09T12:00:00Z", 47.5, -110, -110),
            ("bookb", "2026-09-09T12:00:00Z", 48.5, -110, -110),
        ])
        total = construct_offered_total(conn, gid, "2026-09-10T00:00:00Z")
        self.assertEqual(total["total_points"], 47.5)

    def test_prices_only_from_books_matching_representative_total(self):
        conn = fresh_conn()
        gid = seed_game(conn, [
            ("booka", "2026-09-09T12:00:00Z", 47.5, -115, -105),
            ("bookb", "2026-09-09T12:00:00Z", 47.5, -110, -110),
            ("bookc", "2026-09-09T12:00:00Z", 48.5, 150, 160),
        ])
        total = construct_offered_total(conn, gid, "2026-09-10T00:00:00Z")
        self.assertEqual(total["total_points"], 47.5)
        self.assertIn(total["over_price"], {-115, -110})
        self.assertIn(total["under_price"], {-105, -110})
        self.assertNotIn(total["over_price"], {150})

    def test_missing_price_returns_none_independently(self):
        conn = fresh_conn()
        gid = seed_game(conn, [("booka", "2026-09-09T12:00:00Z", 47.5, None, -110)])
        total = construct_offered_total(conn, gid, "2026-09-10T00:00:00Z")
        self.assertIsNone(total["over_price"])
        self.assertEqual(total["under_price"], -110)

    def test_future_snapshot_does_not_leak_backward(self):
        conn = fresh_conn()
        gid = seed_game(conn, [
            ("booka", "2026-09-09T12:00:00Z", 47.5, -110, -110),
            ("booka", "2026-09-10T06:00:00Z", 52.5, -120, 100),
        ])
        total = construct_offered_total(conn, gid, "2026-09-10T00:00:00Z")
        self.assertEqual(total["total_points"], 47.5)

    def test_no_available_total_returns_none(self):
        conn = fresh_conn()
        gid = seed_game(conn, [])
        self.assertIsNone(construct_offered_total(conn, gid, "2026-09-10T00:00:00Z"))


if __name__ == "__main__":
    unittest.main()
