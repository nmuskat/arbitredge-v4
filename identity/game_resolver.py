"""
identity/game_resolver.py

Resolves a source's reported game (raw home/away team text + kickoff time)
into a canonical_game_id, creating a new canonical game only when genuinely
warranted.

Key behaviors (per approved requirements):

  - Canonical identity = sport + canonical home team + canonical away team
    + kickoff time, but home/away ORDER is not assumed consistent across
    sources: matching checks both orderings, and any mismatch relative to
    the canonical row's stored ordering is recorded (not silently
    corrected) in game_id_map.home_away_matches_canonical.

  - Kickoff-time tolerance: small discrepancies (default 90 minutes)
    between sources reporting "the same" game are tolerated and merged
    into one canonical game. Differences beyond tolerance are treated as
    a genuinely different game (e.g. two teams playing each other twice in
    a season, common in NFL divisional matchups) -- a new canonical game
    is created, not merged.

  - Idempotent re-ingestion: if this exact (source, source_game_key) has
    already been linked, its existing canonical_game_id is returned
    directly. If that same source now reports a materially different
    kickoff time for the same source_game_key, this is treated as a
    RESCHEDULE of the same underlying event: the canonical game's
    commence_time is updated in place, but the change is permanently
    logged to game_reschedule_log first, so provenance is never destroyed
    and all prior raw observations continue pointing at the same
    canonical_game_id.

  - Ambiguous cases (e.g. more than one existing canonical game matches
    the same team-set within the tolerance window -- which should not
    normally happen) are never silently resolved by guessing; they are
    logged and None is returned for manual review.
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from identity.team_resolver import TeamResolver

DEFAULT_KICKOFF_TOLERANCE_MINUTES = 90


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_iso(ts: str) -> datetime:
    ts_normalized = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts_normalized)


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("arbitredge.identity")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    return logger


class GameResolver:
    def __init__(
        self,
        conn: sqlite3.Connection,
        team_resolver: TeamResolver,
        kickoff_tolerance_minutes: int = DEFAULT_KICKOFF_TOLERANCE_MINUTES,
    ):
        self.conn = conn
        self.team_resolver = team_resolver
        self.tolerance = timedelta(minutes=kickoff_tolerance_minutes)
        self.logger = _get_logger()

    def resolve_or_create(
        self,
        source: str,
        source_game_key: str,
        sport: str,
        home_team_raw: str,
        away_team_raw: str,
        commence_time_raw: str,
    ) -> Optional[int]:
        existing_link = self.conn.execute(
            "SELECT canonical_game_id, source_reported_commence_time "
            "FROM game_id_map WHERE source = ? AND source_game_key = ?",
            (source, source_game_key),
        ).fetchone()
        if existing_link:
            canonical_game_id, prior_reported_time = existing_link
            self._maybe_handle_reschedule(canonical_game_id, source, commence_time_raw)
            self._update_game_id_map_report(
                source, source_game_key, home_team_raw, away_team_raw, commence_time_raw
            )
            return canonical_game_id

        home_id = self.team_resolver.resolve(source, home_team_raw, sport)
        away_id = self.team_resolver.resolve(source, away_team_raw, sport)
        if home_id is None or away_id is None:
            self.logger.warning(
                "Cannot resolve game (%s / %s): team resolution failed "
                "(home_raw=%r -> %r, away_raw=%r -> %r). Not creating a game.",
                source, source_game_key, home_team_raw, home_id, away_team_raw, away_id,
            )
            return None

        try:
            new_time = _parse_iso(commence_time_raw)
        except ValueError:
            self.logger.error(
                "Cannot resolve game (%s / %s): unparseable commence_time %r",
                source, source_game_key, commence_time_raw,
            )
            return None

        candidates = self.conn.execute(
            "SELECT canonical_game_id, home_team_id, away_team_id, commence_time "
            "FROM games_canonical WHERE sport = ? "
            "AND ((home_team_id = ? AND away_team_id = ?) "
            "OR (home_team_id = ? AND away_team_id = ?))",
            (sport, home_id, away_id, away_id, home_id),
        ).fetchall()

        within_tolerance = []
        for canonical_game_id, cand_home, cand_away, cand_time_str in candidates:
            try:
                cand_time = _parse_iso(cand_time_str)
            except ValueError:
                continue
            if abs(cand_time - new_time) <= self.tolerance:
                within_tolerance.append((canonical_game_id, cand_home, cand_away))

        if len(within_tolerance) > 1:
            self.logger.error(
                "AMBIGUOUS game match for (%s / %s): %d existing canonical games "
                "match team-set within tolerance. Refusing to guess -- manual review needed.",
                source, source_game_key, len(within_tolerance),
            )
            return None

        if len(within_tolerance) == 1:
            canonical_game_id, cand_home, cand_away = within_tolerance[0]
            matches_order = 1 if cand_home == home_id else 0
            self._link_game_id_map(
                source, source_game_key, canonical_game_id,
                home_team_raw, away_team_raw, commence_time_raw, matches_order,
            )
            return canonical_game_id

        cur = self.conn.execute(
            "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time, odds_api_game_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (sport, home_id, away_id, commence_time_raw, source_game_key if source == "the_odds_api" else None),
        )
        canonical_game_id = cur.lastrowid
        self._link_game_id_map(
            source, source_game_key, canonical_game_id,
            home_team_raw, away_team_raw, commence_time_raw, matches_order=1,
        )
        self.conn.commit()
        return canonical_game_id

    def _link_game_id_map(
        self, source, source_game_key, canonical_game_id,
        home_raw, away_raw, commence_time_raw, matches_order,
    ) -> None:
        self.conn.execute(
            "INSERT INTO game_id_map "
            "(source, source_game_key, canonical_game_id, source_reported_home_raw, "
            "source_reported_away_raw, source_reported_commence_time, "
            "home_away_matches_canonical, first_linked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (source, source_game_key, canonical_game_id, home_raw, away_raw,
             commence_time_raw, matches_order, _now()),
        )
        self.conn.commit()

    def _update_game_id_map_report(
        self, source, source_game_key, home_raw, away_raw, commence_time_raw
    ) -> None:
        self.conn.execute(
            "UPDATE game_id_map SET source_reported_home_raw = ?, "
            "source_reported_away_raw = ?, source_reported_commence_time = ? "
            "WHERE source = ? AND source_game_key = ?",
            (home_raw, away_raw, commence_time_raw, source, source_game_key),
        )
        self.conn.commit()

    def _maybe_handle_reschedule(
        self, canonical_game_id: int, source: str, new_commence_time_raw: str
    ) -> None:
        row = self.conn.execute(
            "SELECT sport, home_team_id, away_team_id, commence_time "
            "FROM games_canonical WHERE canonical_game_id = ?",
            (canonical_game_id,),
        ).fetchone()
        if row is None:
            return

        sport, home_id, away_id, current_time_str = row
        try:
            current_time = _parse_iso(current_time_str)
            new_time = _parse_iso(new_commence_time_raw)
        except ValueError:
            return

        if abs(new_time - current_time) <= self.tolerance:
            return

        # A source-specific event key can legitimately move a game to a kickoff
        # that another source has already used to create a separate canonical
        # identity. Reconcile that collision instead of leaving two canonicals
        # representing the same event.
        candidates = self.conn.execute(
            "SELECT canonical_game_id, home_team_id, away_team_id, commence_time "
            "FROM games_canonical "
            "WHERE canonical_game_id <> ? "
            "AND sport = ? "
            "AND ((home_team_id = ? AND away_team_id = ?) "
            "OR (home_team_id = ? AND away_team_id = ?))",
            (canonical_game_id, sport, home_id, away_id, away_id, home_id),
        ).fetchall()

        collision_candidates = []
        for candidate_id, candidate_home, candidate_away, candidate_time_str in candidates:
            try:
                candidate_time = _parse_iso(candidate_time_str)
            except ValueError:
                continue
            if abs(candidate_time - new_time) <= self.tolerance:
                collision_candidates.append(
                    (candidate_id, candidate_home, candidate_away, candidate_time_str)
                )

        if len(collision_candidates) > 1:
            self.logger.error(
                "AMBIGUOUS reschedule collision for canonical_game_id=%s via source=%s: "
                "%d existing canonical games match the new team-set/kickoff. "
                "Refusing to reconcile -- manual review needed.",
                canonical_game_id, source, len(collision_candidates),
            )
            return

        # The current canonical identity remains the survivor because the
        # source_game_key already established it. The pre-existing canonical
        # at the new kickoff becomes the duplicate/redirect target.
        collision_id = collision_candidates[0][0] if collision_candidates else None

        self.conn.execute("BEGIN")
        try:
            self.conn.execute(
                "INSERT INTO game_reschedule_log "
                "(canonical_game_id, previous_commence_time, new_commence_time, "
                "detected_via_source, detected_at, note) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    canonical_game_id, current_time_str, new_commence_time_raw,
                    source, _now(),
                    (
                        "Detected via repeat ingestion of same source_game_key with a "
                        "materially different commence_time; canonical_game_id preserved."
                        if collision_id is None
                        else
                        "Detected via repeat ingestion of same source_game_key with a "
                        "materially different commence_time; canonical_game_id preserved "
                        "and conflicting canonical identity redirected."
                    ),
                ),
            )

            if collision_id is not None:
                self.conn.execute(
                    "INSERT INTO game_canonical_redirects "
                    "(duplicate_game_id, survivor_game_id, reason, reconciled_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        collision_id,
                        canonical_game_id,
                        "Existing canonical game matched rescheduled team-set and kickoff",
                        _now(),
                    ),
                )

            self.conn.execute(
                "UPDATE games_canonical SET commence_time = ? "
                "WHERE canonical_game_id = ?",
                (new_commence_time_raw, canonical_game_id),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        if collision_id is not None:
            self.logger.info(
                "Reschedule collision reconciled: canonical_game_id=%s survives; "
                "duplicate canonical_game_id=%s redirected via source=%s",
                canonical_game_id, collision_id, source,
            )
        else:
            self.logger.info(
                "Reschedule detected for canonical_game_id=%s via source=%s: %s -> %s",
                canonical_game_id, source, current_time_str, new_commence_time_raw,
            )
