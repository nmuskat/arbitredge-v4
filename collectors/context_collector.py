"""
collectors/context_collector.py

Collects objective, factual per-game context (rankings, records, TV
network, kickoff timing) from ESPN's public scoreboard endpoints. Free,
no API key, zero the-odds-api.com credit cost.

Explicitly NOT this collector's job:
  - No market_snapshots writes -- that table belongs exclusively to
    collectors/odds_collector.py.
  - No is_rivalry / playoff_relevance -- ESPN's scoreboard doesn't reliably
    expose these, and this project does not infer/guess data it doesn't
    actually have. They are left NULL until a reliable source exists.
  - ranking_home / ranking_away stay NULL for NFL (no AP-style poll exists
    for the NFL); only populated for CFB, from ESPN's separate rankings
    endpoint.

ESPN posture: these are undocumented, unofficial public JSON endpoints,
not a guaranteed API contract. Every response is stored raw for
auditability, and any failure here (network, parsing, ESPN changing shape)
is caught and logged as a failed collection RUN -- it never propagates
into or blocks a separately-invoked OddsCollector run. This isolation is
structural (each collector's `collect()` call is independently wrapped in
its own CollectionRun, per base_collector.py), not just a convention.

Append-only / point-in-time correctness: every poll inserts NEW
game_context rows. If a ranking, record, or TV assignment changes between
polls, that's a new observation with a new retrieved_at -- the prior row
is never updated or overwritten, consistent with every other raw table in
this project.
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
ESPN_CFB_RANKINGS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/rankings"

DEFAULT_MIN_LEGITIMATE_POLL_INTERVAL_MINUTES = 30


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _parse_espn_datetime(raw: str) -> datetime:
    """ESPN sometimes omits seconds (e.g. '2026-09-10T20:20Z'); normalize
    before handing to fromisoformat, which is strict about this pre-3.11."""
    s = raw.replace("Z", "+00:00")
    s = re.sub(r"(T\d{2}:\d{2})(?=[+\-]|$)", r"\1:00", s)
    return datetime.fromisoformat(s)


class ContextCollector(BaseCollector):
    PARSER_VERSION = "context_collector_v1"

    def __init__(
        self,
        conn: sqlite3.Connection,
        team_resolver: TeamResolver,
        game_resolver: GameResolver,
        dry_run: bool = False,
        min_legitimate_poll_interval_minutes: int = DEFAULT_MIN_LEGITIMATE_POLL_INTERVAL_MINUTES,
        **base_kwargs,
    ):
        super().__init__(conn, collector_name="context_collector", source="espn", **base_kwargs)
        self.team_resolver = team_resolver
        self.game_resolver = game_resolver
        self.dry_run = dry_run
        self.min_legitimate_poll_interval_minutes = min_legitimate_poll_interval_minutes

    # ---------------------------------------------------------------
    # Idempotency guard (same pattern as OddsCollector, looser default
    # since ESPN has no credit cost -- this mainly guards against a
    # literal accidental double-invocation, not scheduling discipline)
    # ---------------------------------------------------------------

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

    # ---------------------------------------------------------------
    # Parsing helpers
    # ---------------------------------------------------------------

    @staticmethod
    def _parse_rankings(rankings_json: dict) -> dict:
        """Returns {espn_team_id: current_rank} from the first available
        poll in ESPN's rankings response. Missing/unexpected shape simply
        yields an empty dict (rankings are supplementary, not fatal)."""
        result = {}
        try:
            polls = rankings_json.get("rankings", [])
            if not polls:
                return result
            ap_poll = next(
                (poll for poll in polls if str(poll.get("type", "")).lower() == "ap"),
                None,
            )
            if ap_poll is None:
                return result
            ranks = ap_poll.get("ranks", [])
            for entry in ranks:
                team_id = entry.get("team", {}).get("id")
                current = entry.get("current")
                if team_id is not None and current is not None:
                    result[str(team_id)] = int(current)
        except (AttributeError, TypeError, ValueError):
            return {}
        return result

    @staticmethod
    def _extract_record(competitor: dict) -> Optional[str]:
        for r in competitor.get("records", []) or []:
            if r.get("type") == "total":
                return r.get("summary")
        return None

    @staticmethod
    def _extract_tv_network(competition: dict) -> Optional[str]:
        names = []
        for b in competition.get("broadcasts", []) or []:
            names.extend(b.get("names", []) or [])
        if names:
            return ", ".join(dict.fromkeys(names))  # de-dup, preserve order

        geo_names = []
        for g in competition.get("geoBroadcasts", []) or []:
            short_name = (g.get("media") or {}).get("shortName")
            if short_name:
                geo_names.append(short_name)
        if geo_names:
            return ", ".join(dict.fromkeys(geo_names))

        return None

    @staticmethod
    def _classify_kickoff_window(commence_time_raw: str) -> Optional[str]:
        """
        Deterministic, disclosed time-of-day bucket for a KNOWN kickoff
        timestamp -- purely descriptive of when the game is scheduled, not
        a claim about whether it's a nationally-featured/standalone game.

        is_primetime is intentionally NOT derived here or anywhere else in
        this collector: a late kickoff time alone does not establish that a
        game is a standalone/nationally-featured broadcast (a real
        primetime/standalone signal would need kickoff time + network +
        competing-game context together, and should be validated
        empirically before becoming a stored/scored feature -- not silently
        assumed from one heuristic). is_primetime is always inserted as
        NULL until a defensible source/method exists.

        Bucket rule (US Eastern local kickoff hour):
          < 16:00 ET  -> 'early_afternoon'
          16-19:00 ET -> 'late_afternoon'
          >= 19:00 ET -> 'evening'
        Returns None if the local IANA timezone database isn't available,
        rather than guessing with a fixed UTC offset.
        """
        try:
            from zoneinfo import ZoneInfo
            dt_utc = _parse_espn_datetime(commence_time_raw)
            dt_et = dt_utc.astimezone(ZoneInfo("America/New_York"))
        except Exception:
            return None

        hour = dt_et.hour
        if hour < 16:
            return "early_afternoon"
        elif hour < 19:
            return "late_afternoon"
        else:
            return "evening"

    # ---------------------------------------------------------------
    # Main collection entrypoint
    # ---------------------------------------------------------------

    def collect(self, sport: str) -> dict:
        if sport not in ESPN_SCOREBOARD_URLS:
            raise ValueError(f"Unsupported sport {sport!r}")

        summary = {
            "sport": sport,
            "skipped_too_soon": False,
            "games_returned": 0,
            "records_inserted": 0,
            "records_skipped": 0,
            "scoreboard_raw_response_id": None,
            "rankings_raw_response_id": None,
            "rankings_available": False,
        }

        with self.new_run() as run:
            if self._too_soon_since_last_poll():
                run.error_type = "SkippedTooSoon"
                self.logger.info(
                    "Skipping context poll for %s: last poll was within %d minute(s).",
                    sport, self.min_legitimate_poll_interval_minutes,
                )
                summary["skipped_too_soon"] = True
                return summary

            scoreboard_url = ESPN_SCOREBOARD_URLS[sport]
            response = self.request_with_retry(run, "GET", scoreboard_url)
            observed_at = _now()
            raw_response_id = self.store_raw_response(
                url=scoreboard_url, http_status=response.status_code, response_body=response.text,
            )
            summary["scoreboard_raw_response_id"] = raw_response_id

            try:
                data = response.json()
                events = data["events"]
                if not isinstance(events, list):
                    raise ParsingError(f"Expected 'events' to be a list, got {type(events).__name__}")
            except (ValueError, KeyError, TypeError) as e:
                raise ParsingError(f"Malformed ESPN scoreboard response: {e}") from e

            summary["games_returned"] = len(events)

            rankings_by_team_id = {}
            rankings_raw_response_id = None
            if sport == "cfb":
                try:
                    rank_response = self.request_with_retry(run, "GET", ESPN_CFB_RANKINGS_URL)
                    rankings_raw_response_id = self.store_raw_response(
                        url=ESPN_CFB_RANKINGS_URL, http_status=rank_response.status_code,
                        response_body=rank_response.text,
                    )
                    rankings_by_team_id = self._parse_rankings(rank_response.json())
                    summary["rankings_raw_response_id"] = rankings_raw_response_id
                    summary["rankings_available"] = bool(rankings_by_team_id)
                except Exception as e:
                    # Rankings are supplementary. A rankings-specific failure
                    # must not fail the whole context run -- games/records/TV
                    # are still useful without rankings.
                    self.logger.warning(
                        "ESPN rankings fetch/parse failed (non-fatal, continuing "
                        "without rankings for this poll): %s", e,
                    )

            for event in events:
                self._process_event(
                    run, sport, event, observed_at, raw_response_id,
                    rankings_by_team_id, rankings_raw_response_id, summary,
                )

        return summary

    def _process_event(
        self, run: CollectionRun, sport, event, observed_at, raw_response_id,
        rankings_by_team_id, rankings_raw_response_id, summary,
    ) -> None:
        game_id = event.get("id")
        competitions = event.get("competitions") or []
        if not competitions:
            run.records_skipped += 1
            summary["records_skipped"] += 1
            self.logger.warning("Skipping event with no competitions: %r", event.get("id"))
            return
        competition = competitions[0]
        commence_time_raw = event.get("date") or competition.get("date")

        competitors = competition.get("competitors") or []
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)

        if not game_id or not commence_time_raw or home is None or away is None:
            run.records_skipped += 1
            summary["records_skipped"] += 1
            self.logger.warning("Skipping malformed ESPN event (missing required field): %r", game_id)
            return

        home_name = (home.get("team") or {}).get("displayName")
        away_name = (away.get("team") or {}).get("displayName")
        if not home_name or not away_name:
            run.records_skipped += 1
            summary["records_skipped"] += 1
            self.logger.warning("Skipping event %s: missing team display name", game_id)
            return

        canonical_game_id = self.resolve_game_or_skip(
            run, self.game_resolver, "espn", str(game_id), sport, home_name, away_name, commence_time_raw,
        )
        if canonical_game_id is None:
            summary["records_skipped"] += 1
            return

        record_home = self._extract_record(home)
        record_away = self._extract_record(away)

        ranking_home = None
        ranking_away = None
        if sport == "cfb" and rankings_by_team_id:
            home_espn_id = str((home.get("team") or {}).get("id") or "")
            away_espn_id = str((away.get("team") or {}).get("id") or "")
            ranking_home = rankings_by_team_id.get(home_espn_id)
            ranking_away = rankings_by_team_id.get(away_espn_id)

        tv_network = self._extract_tv_network(competition)
        kickoff_window = self._classify_kickoff_window(commence_time_raw)
        is_primetime = None  # never derived -- see _classify_kickoff_window docstring

        methodology_note = "ESPN undocumented public scoreboard endpoint; best-effort, not a guaranteed contract."

        if self.dry_run:
            return

        def _insert(c):
            cur = c.execute(
                "INSERT INTO game_context "
                "(canonical_game_id, observed_at, ranking_home, ranking_away, record_home, record_away, "
                "tv_network, is_primetime, kickoff_window, is_rivalry, playoff_relevance, "
                "google_trends_home, google_trends_away, trends_observed_at, "
                "source, source_url, retrieved_at, source_reported_at, raw_value, "
                "parser_version, source_methodology_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, "
                "?, ?, ?, ?, ?, ?, ?)",
                (
                    canonical_game_id, observed_at, ranking_home, ranking_away, record_home, record_away,
                    tv_network, is_primetime, kickoff_window,
                    "espn", ESPN_SCOREBOARD_URLS[sport], observed_at, commence_time_raw, None,
                    self.PARSER_VERSION, methodology_note,
                ),
            )
            context_id = cur.lastrowid
            # Normalized provenance: link this observation to every raw
            # response that contributed to it, tagged by role. One
            # game_context row can reference multiple raw_responses rows.
            c.execute(
                "INSERT INTO context_raw_response_links (context_id, raw_response_id, role) "
                "VALUES (?, ?, 'scoreboard')",
                (context_id, raw_response_id),
            )
            if rankings_raw_response_id is not None:
                c.execute(
                    "INSERT INTO context_raw_response_links (context_id, raw_response_id, role) "
                    "VALUES (?, ?, 'rankings')",
                    (context_id, rankings_raw_response_id),
                )

        inserted = self.safe_insert(run, _insert, context_note=f"{canonical_game_id}/espn-context")
        if inserted:
            summary["records_inserted"] += 1
        else:
            summary["records_skipped"] += 1
