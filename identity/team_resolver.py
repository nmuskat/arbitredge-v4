"""
identity/team_resolver.py

Resolves free-text team names/abbreviations from any source into a stable
canonical_team_id.

Resolution order (conservative, exact-match-first, NO fuzzy/edit-distance
matching anywhere):

  1. Exact (source, raw_text) match against team_aliases -- this source has
     used this exact string before for this exact team.
  2. Exact raw_text match against any other source's alias for the same
     sport, PROVIDED it is unambiguous (maps to exactly one canonical
     team). If the same exact text is seeded for more than one team
     (should not happen given the seed-time conflict check, but checked
     again at runtime as a safety net), this step refuses to guess.
  3. Normalized-exact match: normalize_text(raw_text) compared against
     normalize_text() of every known alias for the given sport, again only
     if unambiguous. This equalizes case/punctuation/whitespace only (see
     normalize.py) -- it is still exact string comparison, just after a
     conservative normalization pass.
  4. Unresolved: logged to unresolved_aliases (occurrence_count
     incremented on repeat sightings) and None is returned. The resolver
     never guesses at this or any prior step.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from identity.normalize import normalize_text


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class TeamResolver:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._exact_by_source: dict[tuple, str] = {}
        self._exact_any_source: dict[tuple, set] = {}
        self._normalized: dict[tuple, set] = {}
        self._load()

    def _load(self) -> None:
        rows = self.conn.execute(
            "SELECT ta.source, ta.alias_text, ta.canonical_team_id, tc.sport "
            "FROM team_aliases ta JOIN teams_canonical tc "
            "ON ta.canonical_team_id = tc.canonical_team_id"
        ).fetchall()
        for source, alias_text, canonical_team_id, sport in rows:
            self._exact_by_source[(source, alias_text)] = canonical_team_id
            self._exact_any_source.setdefault((sport, alias_text), set()).add(canonical_team_id)
            norm = normalize_text(alias_text)
            self._normalized.setdefault((sport, norm), set()).add(canonical_team_id)

    def resolve(self, source: str, raw_text: str, sport: str) -> Optional[str]:
        if not raw_text or not raw_text.strip():
            return None

        # Step 1: exact (source, raw_text)
        key = (source, raw_text)
        if key in self._exact_by_source:
            return self._exact_by_source[key]

        # Step 2: exact raw_text from any source, if unambiguous
        candidates = self._exact_any_source.get((sport, raw_text))
        if candidates:
            if len(candidates) == 1:
                return next(iter(candidates))
            # ambiguous exact text across teams -- do not guess
            self._log_unresolved(source, raw_text, sport, "ambiguous_exact_match")
            return None

        # Step 3: normalized-exact, if unambiguous
        norm = normalize_text(raw_text)
        norm_candidates = self._normalized.get((sport, norm))
        if norm_candidates:
            if len(norm_candidates) == 1:
                return next(iter(norm_candidates))
            self._log_unresolved(source, raw_text, sport, "ambiguous_normalized_match")
            return None

        # Step 4: unresolved
        self._log_unresolved(source, raw_text, sport, "no_match")
        return None

    def _log_unresolved(self, source: str, raw_text: str, sport: str, reason: str) -> None:
        now = _now()
        norm = normalize_text(raw_text)
        existing = self.conn.execute(
            "SELECT unresolved_id, occurrence_count FROM unresolved_aliases "
            "WHERE sport = ? AND source = ? AND raw_text = ?",
            (sport, source, raw_text),
        ).fetchone()
        if existing:
            unresolved_id, occurrence_count = existing
            self.conn.execute(
                "UPDATE unresolved_aliases SET last_seen_at = ?, occurrence_count = ?, reason = ? "
                "WHERE unresolved_id = ?",
                (now, occurrence_count + 1, reason, unresolved_id),
            )
        else:
            self.conn.execute(
                "INSERT INTO unresolved_aliases "
                "(sport, source, raw_text, normalized_text, reason, first_seen_at, last_seen_at, occurrence_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                (sport, source, raw_text, norm, reason, now, now),
            )
        self.conn.commit()

    def refresh(self) -> None:
        """Re-loads the in-memory index -- call after seeding new aliases."""
        self._exact_by_source.clear()
        self._exact_any_source.clear()
        self._normalized.clear()
        self._load()
