"""
collectors/odds_collector.py

Collects CURRENT (not historical) spreads odds from the-odds-api.com for
NFL and CFB, at the lowest defensible cost.

Scope decisions (see accompanying report for full rationale):
  - Markets: spreads only. h2h + totals would triple the credit cost per
    poll (cost = markets x regions on the current-odds endpoint); not
    worth it until spreads-only has proven the core thesis.
  - Regions: 'us' only.
  - Every bookmaker returned by the region is preserved -- no filtering
    down to a favorite subset at ingestion time.
  - Pregame only: any game whose commence_time has already passed at
    retrieval time is excluded from market_snapshots (but never deletes
    anything already collected for it).

Idempotency note: one OddsCollector instance is expected to poll one sport
repeatedly (see run_odds_discovery.py for the calling pattern), so the
too-soon guard checks this collector instance's own collection_log history
by (collector_name, source) without needing a separate sport column on
collection_log.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from collectors.base_collector import BaseCollector, CollectionRun
from collectors.config import get_env
from collectors.errors import ParsingError
from identity.game_resolver import GameResolver
from identity.team_resolver import TeamResolver

SPORT_KEY_MAP = {
    "nfl": "americanfootball_nfl",
    "cfb": "americanfootball_ncaaf",
    "nfl_preseason": "americanfootball_nfl_preseason",
}

# The-odds-api requires a SEPARATE sport key for NFL preseason
# (americanfootball_nfl_preseason) -- confirmed directly against their
# documentation, not assumed. Team/game IDENTITY, however, must stay
# scoped to the same 'nfl' universe: the teams themselves are identical
# whether it's preseason or regular season, and teams_canonical/
# team_aliases were never seeded with any 'nfl_preseason'-scoped entries
# (nor should they be -- that would be pure duplication). This map
# decouples "which sport_key/market to query and tag the raw observation
# with" from "which team-identity universe to resolve against" -- see
# _process_game below, where the two are deliberately no longer the same
# value. games_canonical/teams_canonical are UNCHANGED and untouched by
# this: every NFL game, preseason or not, still resolves to an ordinary
# sport='nfl' canonical game and canonical teams. Only market_snapshots
# (which already carries its own denormalized sport column) preserves the
# 'nfl_preseason' distinction explicitly.
IDENTITY_SPORT_MAP = {
    "nfl": "nfl",
    "cfb": "cfb",
    "nfl_preseason": "nfl",
}

DEFAULT_REGION = "us"
DEFAULT_MARKETS = "spreads"
DEFAULT_MIN_LEGITIMATE_POLL_INTERVAL_MINUTES = 5


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class OddsCollector(BaseCollector):
    PARSER_VERSION = "odds_collector_v1"
    BASE_URL_TEMPLATE = "https://api.the-odds-api.com/v4/sports/{sport_key}/odds"

    def __init__(
        self,
        conn: sqlite3.Connection,
        team_resolver: TeamResolver,
        game_resolver: GameResolver,
        api_key: Optional[str] = None,
        region: str = DEFAULT_REGION,
        markets: str = DEFAULT_MARKETS,
        dry_run: bool = False,
        min_legitimate_poll_interval_minutes: int = DEFAULT_MIN_LEGITIMATE_POLL_INTERVAL_MINUTES,
        **base_kwargs,
    ):
        super().__init__(conn, collector_name="odds_collector", source="the_odds_api", **base_kwargs)
        self.team_resolver = team_resolver
        self.game_resolver = game_resolver
        # Required only for a real (non-dry-run) request; mocked tests can
        # construct this collector with no key at all since the HTTP layer
        # is mocked and never actually reads it.
        self.api_key = api_key if api_key is not None else get_env("THE_ODDS_API_KEY", required=True)
        self.region = region
        self.markets = markets
        self.dry_run = dry_run
        self.min_legitimate_poll_interval_minutes = min_legitimate_poll_interval_minutes

    # ---------------------------------------------------------------
    # Provider-specific usage-header extraction (the base class has no
    # provider-specific logic; this override lives entirely here)
    # ---------------------------------------------------------------

    def secret_values(self) -> tuple:
        return (self.api_key,) if self.api_key else ()

    def extract_usage_info(self, response) -> Optional[dict]:
        headers = response.headers
        info = {}
        for h in ("x-requests-remaining", "x-requests-used", "x-requests-last"):
            if h in headers:
                info[h] = headers[h]
        return info or None

    # ---------------------------------------------------------------
    # Idempotency guard: skip (before spending an API credit) if the last
    # REAL poll by this collector instance was too recent to be a
    # legitimate new scheduled observation opportunity.
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
    # Validation
    # ---------------------------------------------------------------

    @staticmethod
    def _validate_spread_outcomes(home_team_raw: str, away_team_raw: str, outcomes) -> tuple:
        """Returns (is_valid, reason_if_invalid, parsed_dict_if_valid)."""
        if not isinstance(outcomes, list) or len(outcomes) != 2:
            return False, f"expected exactly 2 outcomes, got {outcomes!r}", None

        names = [o.get("name") for o in outcomes]
        if names[0] == names[1]:
            return False, f"both outcomes reference the same team ({names[0]!r})", None
        if set(names) != {home_team_raw, away_team_raw}:
            return False, (
                f"outcome names {names!r} do not match home/away team names "
                f"({home_team_raw!r} / {away_team_raw!r})"
            ), None

        parsed = {}
        for outcome in outcomes:
            name = outcome.get("name")
            price = outcome.get("price")
            point = outcome.get("point")

            if isinstance(price, bool) or not isinstance(price, (int, float)) or price == 0 or abs(price) < 100:
                return False, f"malformed price for {name!r}: {price!r}", None
            if isinstance(point, bool) or not isinstance(point, (int, float)):
                return False, f"malformed/missing point for {name!r}: {point!r}", None

            side = "home" if name == home_team_raw else "away"
            parsed[f"spread_{side}"] = float(point)
            parsed[f"spread_price_{side}"] = float(price)

        return True, None, parsed

    @staticmethod
    def _validate_total_outcomes(outcomes):
        """Validate a two-sided Over/Under market from The Odds API."""
        import math

        if not isinstance(outcomes, list) or len(outcomes) != 2:
            return False, "expected exactly two total outcomes", None

        parsed = {}
        points = []

        for outcome in outcomes:
            if not isinstance(outcome, dict):
                return False, "malformed total outcome", None

            side = outcome.get("name")
            price = outcome.get("price")
            point = outcome.get("point")

            if side not in ("Over", "Under") or side in parsed:
                return False, "expected one Over and one Under", None

            if (isinstance(price, bool) or
                    not isinstance(price, (int, float)) or
                    not math.isfinite(price) or
                    price == 0 or abs(price) < 100):
                return False, "invalid total price", None

            if (isinstance(point, bool) or
                    not isinstance(point, (int, float)) or
                    not math.isfinite(point) or point <= 0):
                return False, "invalid total points", None

            parsed[side] = float(price)
            points.append(float(point))

        if set(parsed) != {"Over", "Under"} or points[0] != points[1]:
            return False, "missing side or mismatched total lines", None

        return True, None, {
            "total_points": points[0],
            "total_price_over": parsed["Over"],
            "total_price_under": parsed["Under"],
        }

    @staticmethod
    def _is_pregame(commence_time_raw: str, observed_at_raw: str) -> bool:
        try:
            commence = _parse_iso(commence_time_raw)
            observed = _parse_iso(observed_at_raw)
        except (ValueError, TypeError, AttributeError):
            return False
        return commence > observed

    # ---------------------------------------------------------------
    # Main collection entrypoint
    # ---------------------------------------------------------------

    def collect(self, sport: str) -> dict:
        if sport not in SPORT_KEY_MAP:
            raise ValueError(f"Unsupported sport {sport!r}; expected one of {list(SPORT_KEY_MAP)}")

        summary = {
            "sport": sport,
            "skipped_too_soon": False,
            "games_returned": 0,
            "bookmakers_seen": set(),
            "records_inserted": 0,
            "records_skipped": 0,
            "already_started_skipped": 0,
            "dry_run": self.dry_run,
            "dry_run_would_insert": [],
            "usage_info": None,
            "raw_response_id": None,
        }

        with self.new_run() as run:
            if self._too_soon_since_last_poll():
                run.error_type = "SkippedTooSoon"
                self.logger.info(
                    "Skipping poll for %s: last poll was within %d minute(s), "
                    "treating this as an accidental rerun rather than a new "
                    "scheduled observation opportunity.",
                    sport, self.min_legitimate_poll_interval_minutes,
                )
                summary["skipped_too_soon"] = True
                return summary

            sport_key = SPORT_KEY_MAP[sport]
            url = self.BASE_URL_TEMPLATE.format(sport_key=sport_key)
            params = {
                "apiKey": self.api_key,
                "regions": self.region,
                "markets": self.markets,
                "oddsFormat": "american",
            }
            # Never construct a loggable/storable URL that contains the real
            # key in the first place -- simpler and safer than redacting
            # after the fact.
            display_url = f"{url}?regions={self.region}&markets={self.markets}&oddsFormat=american"

            response = self.request_with_retry(run, "GET", url, params=params)
            observed_at = _now()

            summary["usage_info"] = self.extract_usage_info(response)

            raw_response_id = self.store_raw_response(
                url=display_url,
                http_status=response.status_code,
                response_body=response.text,
                request_params_redacted=f"regions={self.region}&markets={self.markets}",
            )
            summary["raw_response_id"] = raw_response_id

            try:
                games = response.json()
            except ValueError as e:
                raise ParsingError(f"Could not parse response JSON: {e}") from e

            if not isinstance(games, list):
                raise ParsingError(f"Expected a list of games, got {type(games).__name__}")

            summary["games_returned"] = len(games)

            for game in games:
                self._process_game(run, sport, game, observed_at, raw_response_id, summary)

        return summary

    def _process_game(self, run: CollectionRun, sport, game, observed_at, raw_response_id, summary) -> None:
        game_id = game.get("id")
        home_team_raw = game.get("home_team")
        away_team_raw = game.get("away_team")
        commence_time_raw = game.get("commence_time")

        if not game_id or not home_team_raw or not away_team_raw or not commence_time_raw:
            run.records_skipped += 1
            summary["records_skipped"] += 1
            self.logger.warning("Skipping malformed game record (missing required field): %r", game)
            return

        if not self._is_pregame(commence_time_raw, observed_at):
            run.records_skipped += 1
            summary["records_skipped"] += 1
            summary["already_started_skipped"] += 1
            self.logger.info(
                "Skipping already-started game %s (%s vs %s, commence_time=%s): "
                "pregame collection only. Earlier pregame observations, if any, are untouched.",
                game_id, home_team_raw, away_team_raw, commence_time_raw,
            )
            return

        identity_sport = IDENTITY_SPORT_MAP.get(sport, sport)
        canonical_game_id = self.resolve_game_or_skip(
            run, self.game_resolver, "the_odds_api", game_id, identity_sport,
            home_team_raw, away_team_raw, commence_time_raw,
        )
        if canonical_game_id is None:
            summary["records_skipped"] += 1
            return

        for bookmaker in game.get("bookmakers", []):
            self._process_bookmaker(
                run, sport, canonical_game_id, home_team_raw, away_team_raw,
                commence_time_raw, observed_at, raw_response_id, bookmaker, summary,
            )

    def _process_bookmaker(
        self, run, sport, canonical_game_id, home_team_raw, away_team_raw,
        commence_time_raw, observed_at, raw_response_id, bookmaker, summary,
    ) -> None:
        book_key = bookmaker.get("key")
        book_last_update = bookmaker.get("last_update")
        if not book_key:
            run.records_skipped += 1
            summary["records_skipped"] += 1
            self.logger.warning("Skipping bookmaker entry with no key: %r", bookmaker)
            return

        spread_data = None
        total_data = None

        for market in bookmaker.get("markets", []):
            key = market.get("key")
            outcomes = market.get("outcomes", [])

            if key == "spreads":
                valid, reason, parsed = self._validate_spread_outcomes(
                    home_team_raw, away_team_raw, outcomes
                )
                if valid:
                    spread_data = parsed
                else:
                    run.records_skipped += 1
                    summary["records_skipped"] += 1
                    self.logger.warning(
                        "Invalid spread market for game=%s book=%s: %s",
                        canonical_game_id, book_key, reason,
                    )

            elif key == "totals":
                valid, reason, parsed = self._validate_total_outcomes(outcomes)
                if valid:
                    total_data = parsed
                else:
                    run.records_skipped += 1
                    summary["records_skipped"] += 1
                    self.logger.warning(
                        "Invalid totals market for game=%s book=%s: %s",
                        canonical_game_id, book_key, reason,
                    )

        if spread_data is None and total_data is None:
            return

        parsed = {
            "spread_home": None,
            "spread_away": None,
            "spread_price_home": None,
            "spread_price_away": None,
            "total_points": None,
            "total_price_over": None,
            "total_price_under": None,
        }
        if spread_data is not None:
            parsed.update(spread_data)
        if total_data is not None:
            totals_allowed = True

            if sport == "cfb":
                ranking = self.conn.execute(
                    """
                    SELECT gc.ranking_home, gc.ranking_away
                    FROM game_context AS gc
                    WHERE (
                        gc.canonical_game_id = ?
                        OR gc.canonical_game_id IN (
                            SELECT duplicate_game_id
                            FROM game_canonical_redirects
                            WHERE survivor_game_id = ?
                        )
                    )
                    AND gc.observed_at <= ?
                    ORDER BY gc.observed_at DESC, gc.context_id DESC
                    LIMIT 1
                    """,
                    (canonical_game_id, canonical_game_id, observed_at),
                ).fetchone()

                totals_allowed = (
                    ranking is not None
                    and any(
                        isinstance(rank, int) and 1 <= rank <= 25
                        for rank in ranking
                    )
                )

            if totals_allowed:
                parsed.update(total_data)

        if all(value is None for value in parsed.values()):
            return

        summary["bookmakers_seen"].add(book_key)

        if self.dry_run:
            summary["dry_run_would_insert"].append({
                "canonical_game_id": canonical_game_id,
                "book": book_key,
                "book_last_update": book_last_update,
                **parsed,
            })
            return

        def _insert(c):
            c.execute(
                "INSERT INTO market_snapshots "
                "(canonical_game_id, sport, home_team, away_team, commence_time, "
                "book, observed_at, book_last_update, spread_home, spread_away, "
                "spread_price_home, spread_price_away, total_points, "
                "total_price_over, total_price_under, parser_version, raw_response_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    canonical_game_id, sport, home_team_raw, away_team_raw,
                    commence_time_raw, book_key, observed_at, book_last_update,
                    parsed["spread_home"], parsed["spread_away"],
                    parsed["spread_price_home"], parsed["spread_price_away"],
                    parsed["total_points"], parsed["total_price_over"],
                    parsed["total_price_under"], self.PARSER_VERSION,
                    raw_response_id,
                ),
            )

        inserted = self.safe_insert(
            run, _insert, context_note=f"{canonical_game_id}/{book_key}"
        )
        if inserted:
            summary["records_inserted"] += 1
        else:
            summary["records_skipped"] += 1
