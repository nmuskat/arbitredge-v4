"""
Regression tests for standalone-NFL Public Pressure.

These tests protect the historical-reference path from future leakage
and preserve the existing conservative downstream decision rules.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from book_need.public_pressure import _find_historical_nfl_reference_games
from behavioral._stats import reference_zscore, reference_percentile_rank


def test_standalone_nfl_historical_reference_excludes_future_games():
    conn = sqlite3.connect(":memory:")

    conn.executescript("""
    CREATE TABLE games_canonical (
        canonical_game_id INTEGER PRIMARY KEY,
        sport TEXT NOT NULL,
        commence_time TEXT NOT NULL
    );

    CREATE TABLE game_results (
        canonical_game_id INTEGER NOT NULL,
        season_type INTEGER,
        is_final INTEGER NOT NULL,
        retrieved_at TEXT NOT NULL
    );

    INSERT INTO games_canonical VALUES
        (1, 'nfl', '2025-09-12T00:15:00Z'),
        (2, 'nfl', '2025-10-01T00:15:00Z'),
        (3, 'nfl', '2026-09-10T00:15:00Z'),
        (4, 'nfl', '2025-11-01T00:15:00Z'),
        (5, 'cfb', '2025-10-01T00:15:00Z'),
        (6, 'nfl', '2025-12-01T00:15:00Z');

    INSERT INTO game_results VALUES
        (1, 2, 1, '2025-09-12T04:00:00Z'),
        (2, 2, 1, '2025-10-01T04:00:00Z'),
        (3, 2, 1, '2026-09-10T04:00:00Z'),
        (4, 2, 0, '2025-11-01T04:00:00Z'),
        (5, 2, 1, '2025-10-01T04:00:00Z'),
        (6, 2, 1, '2026-09-09T20:00:00Z');
    """)

    rows = _find_historical_nfl_reference_games(
        conn,
        target_commence_time="2026-09-10T00:15:00Z",
        target_cutoff="2026-09-09T00:15:00Z",
    )

    ids = [row[0] for row in rows]

    assert ids == [1, 2]
    assert 3 not in ids
    assert 4 not in ids
    assert 5 not in ids
    assert 6 not in ids

    conn.close()

    print("PASS: standalone NFL historical reference is final, regular-season, prior, and known as-of cutoff")


def test_reference_zscore_excludes_target_from_distribution():
    refs = [0.0, 2.0, 4.0]
    result = reference_zscore(6.0, refs)

    # Reference-only mean=2, population stdev=sqrt(8/3).
    expected = (6.0 - 2.0) / ((8.0 / 3.0) ** 0.5)

    assert abs(result - expected) < 1e-12
    print("PASS: reference z-score standardizes target against reference values only")


def test_reference_percentile_rank_does_not_insert_target():
    refs = [10.0, 20.0, 30.0, 40.0]

    assert reference_percentile_rank(25.0, refs) == 50.0
    assert reference_percentile_rank(40.0, refs) == 100.0
    assert reference_percentile_rank(50.0, refs) == 100.0

    print("PASS: reference percentile ranks target against fixed reference distribution")

if __name__ == "__main__":
    test_standalone_nfl_historical_reference_excludes_future_games()
    test_reference_zscore_excludes_target_from_distribution()
    test_reference_percentile_rank_does_not_insert_target()
