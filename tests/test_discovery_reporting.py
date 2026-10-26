"""
Tests for collectors/run_odds_discovery.py's unresolved-alias classification.

Reproduces the exact scenario reported: a stale unresolved_aliases row from
a prior run (before a seed update) must NOT be reported as a current
failure once the resolver actually succeeds, but must still be visible as
preserved historical audit data, never deleted.

Run with: python tests/test_discovery_reporting.py
"""

import json
import shutil
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collectors.odds_collector import OddsCollector  # noqa: E402
from collectors.run_odds_discovery import classify_unresolved_aliases, _now  # noqa: E402
from db.migrate import run_migration  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402
from identity.game_resolver import GameResolver  # noqa: E402

TEST_DIR = Path(__file__).parent / "_discovery_reporting_test_workspace"


def fresh_conn() -> sqlite3.Connection:
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, headers=None):
        self.status_code = status_code
        self._json_body = json_body
        self.text = json.dumps(json_body) if json_body is not None else ""
        self.headers = headers or {}

    def json(self):
        return self._json_body


def test_stale_historical_row_not_reported_as_current_failure():
    """Reproduces the exact bug: a resolver that now succeeds must show
    0 unresolved for THIS RUN, while the stale row from before the seed fix
    remains visible as historical audit data, not deleted."""
    conn = fresh_conn()

    # Simulate the exact situation: a row logged by a PRIOR run, before the
    # seed update, for a team that the CURRENT (updated) seed resolves fine.
    old_ts = "2026-08-01T00:00:00.000000Z"
    conn.execute(
        "INSERT INTO unresolved_aliases (sport, source, raw_text, normalized_text, reason, "
        "first_seen_at, last_seen_at, occurrence_count) VALUES "
        "('cfb', 'the_odds_api', 'Boise State Broncos', 'boise state broncos', 'no_match', ?, ?, 1)",
        (old_ts, old_ts),
    )
    conn.commit()

    # Confirm the seed genuinely resolves this now (Boise State is in the
    # updated seed data used by the real project at this point)
    tr = TeamResolver(conn)
    assert tr.resolve("the_odds_api", "Boise State Broncos", "cfb") == "cfb_boise_state"

    game_resolver = GameResolver(conn, tr)
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [{
        "id": "evt_1", "sport_key": "americanfootball_ncaaf",
        "commence_time": "2099-09-10T20:20:00Z",
        "home_team": "Boise State Broncos", "away_team": "Air Force Falcons",
        "bookmakers": [{
            "key": "draftkings", "title": "DraftKings", "last_update": "2026-09-08T12:00:00Z",
            "markets": [{"key": "spreads", "outcomes": [
                {"name": "Boise State Broncos", "price": -150, "point": -3.0},
                {"name": "Air Force Falcons", "price": 130, "point": 3.0},
            ]}],
        }],
    }])
    collector = OddsCollector(
        conn=conn, team_resolver=tr, game_resolver=game_resolver,
        api_key="test", session=session, sleep_fn=lambda s: None,
        dry_run=True, min_legitimate_poll_interval_minutes=0,
    )

    since = _now()
    summary = collector.collect("cfb")
    assert summary["records_skipped"] == 0, "resolution should succeed cleanly with the updated seed"

    this_run, historical = classify_unresolved_aliases(conn, "cfb", since)

    assert this_run == [], "nothing should be reported as unresolved THIS RUN -- resolution succeeded"
    assert len(historical) == 1, "the stale prior-run row must still be visible as historical"
    assert historical[0][1] == "Boise State Broncos"
    assert historical[0][5] == old_ts, "the historical row's last_seen_at must be untouched"

    # Confirm nothing was deleted -- the raw row is still exactly there
    remaining = conn.execute("SELECT COUNT(*) FROM unresolved_aliases WHERE sport='cfb'").fetchone()[0]
    assert remaining == 1, "historical rows must be preserved, never deleted, just correctly classified"

    conn.close()
    print("PASS: a stale historical unresolved_aliases row is correctly excluded from "
          "'this run' reporting, remains visible as preserved history, and is never deleted")


def test_genuine_new_failure_this_run_is_correctly_classified():
    """A real, current failure (not present before) must land in 'this run', distinct
    from any historical rows that might also exist for other teams."""
    conn = fresh_conn()

    # Seed one unrelated historical row first
    old_ts = "2026-08-01T00:00:00.000000Z"
    conn.execute(
        "INSERT INTO unresolved_aliases (sport, source, raw_text, normalized_text, reason, "
        "first_seen_at, last_seen_at, occurrence_count) VALUES "
        "('cfb', 'the_odds_api', 'Some Old Fixed Team', 'some old fixed team', 'no_match', ?, ?, 1)",
        (old_ts, old_ts),
    )
    conn.commit()

    tr = TeamResolver(conn)
    game_resolver = GameResolver(conn, tr)
    session = MagicMock()
    # "Miami" is deliberately ambiguous and will genuinely fail to resolve right now
    session.request.return_value = FakeResponse(200, [{
        "id": "evt_2", "sport_key": "americanfootball_ncaaf",
        "commence_time": "2099-09-10T20:20:00Z",
        "home_team": "Miami", "away_team": "Ohio State Buckeyes",
        "bookmakers": [],
    }])
    collector = OddsCollector(
        conn=conn, team_resolver=tr, game_resolver=game_resolver,
        api_key="test", session=session, sleep_fn=lambda s: None,
        dry_run=True, min_legitimate_poll_interval_minutes=0,
    )

    since = _now()
    summary = collector.collect("cfb")
    assert summary["records_skipped"] >= 1

    this_run, historical = classify_unresolved_aliases(conn, "cfb", since)

    this_run_texts = {r[1] for r in this_run}
    historical_texts = {r[1] for r in historical}

    assert "Miami" in this_run_texts, "the genuine current failure must be in this-run results"
    assert "Some Old Fixed Team" in historical_texts, "the pre-existing historical row must remain historical"
    assert "Some Old Fixed Team" not in this_run_texts
    assert "Miami" not in historical_texts

    conn.close()
    print("PASS: a genuine new failure during this run is correctly separated from "
          "pre-existing historical rows for other teams")


if __name__ == "__main__":
    test_stale_historical_row_not_reported_as_current_failure()
    test_genuine_new_failure_this_run_is_correctly_classified()
    print("\nAll discovery-reporting tests passed.")
