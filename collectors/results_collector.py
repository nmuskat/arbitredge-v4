"""
collectors/results_collector.py

Collects TERMINAL game outcomes (final, postponed, canceled) from ESPN's
public scoreboard endpoints -- the same free, undocumented, best-effort
source already used by context_collector.py, just with date/week
parameters for point-in-time and (later, separately) backfill queries.

Explicitly NOT this collector's job:
  - No live/in-play score tracking. Only terminal statuses are stored.
    An in-progress game produces zero rows here.
  - No rolling/derived features (streaks, scoring averages). This table
    stores one row of raw, single-game outcome data per observed change.
  - No ATS grading. That requires joining a final score against a spread
    from market_snapshots at a chosen pregame timestamp -- a separate,
    not-yet-designed step.
  - No historical backfill in this file. This collector is validated
    against current/recent results first; backfill is a deliberately
    separate next step, run only after this is proven correct.

Append-only / correction handling: a game_results row is NEVER updated.
If a later poll of an already-terminal game shows a materially different
result (a rare scorekeeping correction), a NEW row is appended and the
old one is left untouched -- "current best-known result" is simply the
most recent row for that canonical_game_id, a read-time query, not a
mutable pointer.

Duplicate prevention has two layers:
  1. The same too-soon-since-last-poll guard used by every other
     collector in this project (time-gate, protects against accidental
     reruns).
  2. A CONTENT-level check: before inserting, compare the newly parsed
     terminal result against the most recently stored result for that
     canonical_game_id across the fields that define "the result"
     (status, is_final, both scores, winner, overtime, neutral-site,
     season/week). If materially identical, skip -- no duplicate row.
     This is an explicit field-by-field comparison, not a hash, so it
     stays auditable (see _results_materially_equal below).

Duplicate-poll PROVENANCE (Option A): a later poll that confirms an
UNCHANGED terminal result still makes a real HTTP request and gets its
own raw_responses row -- that evidence ("this result was independently
observed again at time T2") is preserved by linking the new
raw_response_id to the EXISTING game_results row in
game_results_raw_response_links, tagged with role='scoreboard_confirmation'
(distinct from role='scoreboard', which marks the response that actually
produced the result row). No new game_results row is ever created by a
confirmation -- game_results still holds exactly one row per distinct
observed result, while game_results_raw_response_links can hold many rows
against that same result_id, each representing one independent raw
observation of it (first-seen plus every later reconfirmation).

Note: this confirmation-link write goes through the same safe_insert
path as every other DB write, which increments collection_log's
records_captured counter. That means records_captured reflects total
successful writes this run (new results AND confirmation links), not
specifically new results -- use the returned summary dict's
'results_inserted' for that.
"""

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from collectors.base_collector import BaseCollector, CollectionRun
from collectors.errors import ParsingError
from identity.game_resolver import GameResolver
from identity.team_resolver import TeamResolver

ESPN_SCOREBOARD_URLS = {
    "nfl": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "cfb": "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
}

DEFAULT_MIN_LEGITIMATE_POLL_INTERVAL_MINUTES = 30

TERMINAL_STATUSES = {
    "STATUS_FINAL",
    "STATUS_POSTPONED",
    "STATUS_CANCELED",
}

RESULT_COMPARISON_FIELDS = [
    "game_status", "is_final", "final_score_home", "final_score_away",
    "winner_home", "is_overtime", "is_neutral_site",
    "season_year", "season_type", "week_number",
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _parse_espn_datetime(raw: str) -> datetime:
    s = raw.replace("Z", "+00:00")
    s = re.sub(r"(T\d{2}:\d{2})(?=[+\-]|$)", r"\1:00", s)
    return datetime.fromisoformat(s)


class ResultsCollector(BaseCollector):
    PARSER_VERSION = "results_collector_v1"

    def __init__(
        self,
        conn: sqlite3.Connection,
        team_resolver: TeamResolver,
        game_resolver: GameResolver,
        dry_run: bool = False,
        min_legitimate_poll_interval_minutes: int = DEFAULT_MIN_LEGITIMATE_POLL_INTERVAL_MINUTES,
        **base_kwargs,
    ):
        super().__init__(conn, collector_name="results_collector", source="espn", **base_kwargs)
        self.team_resolver = team_resolver
        self.game_resolver = game_resolver
        self.dry_run = dry_run
        self.min_legitimate_poll_interval_minutes = min_legitimate_poll_interval_minutes

    def _too_soon_since_last_poll(self) -> bool:
        row = self.conn.execute(
            "SELECT executed_at FROM collection_log "
            "WHERE collector_name = ? AND source = ? "
            "AND (error_type IS NULL OR error_type != 'SkippedTooSoon') "
            "ORDER BY executed_at DESC LIMIT 1",
            (self.collector_name, self.source),
        ).fetchone()
        if row is None:
            return False
        last_time = _parse_iso(row[0])
        now = _parse_iso(_now())
        return (now - last_time) < timedelta(minutes=self.min_legitimate_poll_interval_minutes)

    @staticmethod
    def _extract_result_fields(event: dict) -> Optional[dict]:
        competitions = event.get("competitions") or []
        if not competitions:
            return None
        competition = competitions[0]
        competitors = competition.get("competitors") or []
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if home is None or away is None:
            return None

        status = (event.get("status") or competition.get("status") or {})
        status_type = status.get("type", {}) or {}
        game_status = status_type.get("name")
        if not game_status:
            return None
        is_final = 1 if status_type.get("completed") is True else 0

        def _score(c):
            raw = c.get("score")
            if raw is None:
                return None
            try:
                return int(raw)
            except (ValueError, TypeError):
                return None

        final_score_home = _score(home)
        final_score_away = _score(away)

        winner_home = None
        if home.get("winner") is True:
            winner_home = 1
        elif away.get("winner") is True:
            winner_home = 0

        period = status.get("period")
        detail = status_type.get("detail") or ""
        is_overtime = None
        if isinstance(period, int):
            is_overtime = 1 if (period > 4 or "OT" in detail) else 0
        elif "OT" in detail:
            is_overtime = 1

        is_neutral_site = None
        if "neutralSite" in competition:
            is_neutral_site = 1 if competition.get("neutralSite") is True else 0

        season = event.get("season") or {}
        week = event.get("week") or {}

        return {
            "game_status": game_status,
            "is_final": is_final,
            "final_score_home": final_score_home,
            "final_score_away": final_score_away,
            "winner_home": winner_home,
            "is_overtime": is_overtime,
            "is_neutral_site": is_neutral_site,
            "season_year": season.get("year"),
            "season_type": season.get("type"),
            "week_number": week.get("number"),
            "venue_name": (competition.get("venue") or {}).get("fullName"),
            "espn_status_detail": detail or None,
        }

    @staticmethod
    def _results_materially_equal(a: dict, b: dict) -> bool:
        return all(a.get(f) == b.get(f) for f in RESULT_COMPARISON_FIELDS)

    def collect(self, sport: str, dates: Optional[str] = None) -> dict:
        if sport not in ESPN_SCOREBOARD_URLS:
            raise ValueError(f"Unsupported sport {sport!r}")

        summary = {
            "sport": sport,
            "skipped_too_soon": False,
            "games_returned": 0,
            "results_inserted": 0,
            "results_unchanged_skipped": 0,
            "duplicate_observations_linked": 0,
            "non_terminal_skipped": 0,
            "records_skipped": 0,
            "raw_response_id": None,
        }

        with self.new_run() as run:
            if self._too_soon_since_last_poll():
                run.error_type = "SkippedTooSoon"
                self.logger.info(
                    "Skipping results poll for %s: last poll was within %d minute(s).",
                    sport, self.min_legitimate_poll_interval_minutes,
                )
                summary["skipped_too_soon"] = True
                return summary

            url = ESPN_SCOREBOARD_URLS[sport]
            params = {"dates": dates} if dates else None
            response = self.request_with_retry(run, "GET", url, params=params)
            observed_at = _now()

            raw_response_id = self.store_raw_response(
                url=response.url if hasattr(response, "url") and response.url else url,
                http_status=response.status_code,
                response_body=response.text,
            )
            summary["raw_response_id"] = raw_response_id

            try:
                data = response.json()
                events = data["events"]
                if not isinstance(events, list):
                    raise ParsingError(f"Expected 'events' to be a list, got {type(events).__name__}")
            except (ValueError, KeyError, TypeError) as e:
                raise ParsingError(f"Malformed ESPN scoreboard response: {e}") from e

            summary["games_returned"] = len(events)

            for event in events:
                self._process_event(run, sport, event, observed_at, raw_response_id, summary)

        return summary

    def _process_event(self, run: CollectionRun, sport, event, observed_at, raw_response_id, summary) -> None:
        game_id = event.get("id")
        competitions = event.get("competitions") or []
        commence_time_raw = event.get("date") or (competitions[0].get("date") if competitions else None)

        parsed = self._extract_result_fields(event)
        if parsed is None:
            run.records_skipped += 1
            summary["records_skipped"] += 1
            self.logger.warning("Skipping malformed ESPN event (missing required field): %r", game_id)
            return

        if parsed["game_status"] not in TERMINAL_STATUSES:
            summary["non_terminal_skipped"] += 1
            return

        competitors = competitions[0].get("competitors") or []
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        home_name = (home.get("team") or {}).get("displayName") if home else None
        away_name = (away.get("team") or {}).get("displayName") if away else None

        if not game_id or not commence_time_raw or not home_name or not away_name:
            run.records_skipped += 1
            summary["records_skipped"] += 1
            self.logger.warning("Skipping malformed ESPN event (missing identity field): %r", game_id)
            return

        canonical_game_id = self.resolve_game_or_skip(
            run, self.game_resolver, "espn", str(game_id), sport, home_name, away_name, commence_time_raw,
        )
        if canonical_game_id is None:
            summary["records_skipped"] += 1
            return

        existing = self.conn.execute(
            "SELECT result_id, game_status, is_final, final_score_home, final_score_away, winner_home, "
            "is_overtime, is_neutral_site, season_year, season_type, week_number "
            "FROM game_results WHERE canonical_game_id = ? "
            "ORDER BY retrieved_at DESC LIMIT 1",
            (canonical_game_id,),
        ).fetchone()

        if existing is not None:
            existing_result_id = existing[0]
            existing_dict = dict(zip(RESULT_COMPARISON_FIELDS, existing[1:]))
            if self._results_materially_equal(existing_dict, parsed):
                summary["results_unchanged_skipped"] += 1
                if not self.dry_run:
                    def _link_confirmation(c, existing_result_id=existing_result_id):
                        c.execute(
                            "INSERT INTO game_results_raw_response_links "
                            "(result_id, raw_response_id, role) VALUES (?, ?, 'scoreboard_confirmation')",
                            (existing_result_id, raw_response_id),
                        )
                    linked = self.safe_insert(
                        run, _link_confirmation, context_note=f"{canonical_game_id}/duplicate-confirmation-link"
                    )
                    if linked:
                        summary["duplicate_observations_linked"] += 1
                return

        if self.dry_run:
            return

        def _insert(c):
            cur = c.execute(
                "INSERT INTO game_results "
                "(canonical_game_id, observed_at, game_status, is_final, final_score_home, "
                "final_score_away, winner_home, is_overtime, is_neutral_site, season_year, "
                "season_type, week_number, venue_name, espn_status_detail, "
                "source, source_url, retrieved_at, source_reported_at, parser_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    canonical_game_id, observed_at, parsed["game_status"], parsed["is_final"],
                    parsed["final_score_home"], parsed["final_score_away"], parsed["winner_home"],
                    parsed["is_overtime"], parsed["is_neutral_site"], parsed["season_year"],
                    parsed["season_type"], parsed["week_number"], parsed["venue_name"],
                    parsed["espn_status_detail"],
                    "espn", ESPN_SCOREBOARD_URLS[sport], observed_at, commence_time_raw,
                    self.PARSER_VERSION,
                ),
            )
            result_id = cur.lastrowid
            c.execute(
                "INSERT INTO game_results_raw_response_links (result_id, raw_response_id, role) "
                "VALUES (?, ?, 'scoreboard')",
                (result_id, raw_response_id),
            )

        inserted = self.safe_insert(run, _insert, context_note=f"{canonical_game_id}/espn-result")
        if inserted:
            summary["results_inserted"] += 1
        else:
            summary["records_skipped"] += 1
