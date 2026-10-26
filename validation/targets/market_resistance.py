"""
validation/targets/market_resistance.py

ResistanceDivergenceTarget stays DORMANT, per explicit instruction --
"resistance" and "confirmation" are inherently relative to a public-
pressure estimate that doesn't exist yet; implementing it now would mean
quietly inventing a placeholder baseline.

ReversalTarget is now implemented -- a purely mechanical definition,
per requirement 8 ("defined mechanically from an observed path, not from
an interpretation of intent"):

  Take the book's FULL raw pregame observation sequence (not just the 5
  named checkpoints -- a reversal could occur between two named
  checkpoints and be invisible if only the sparse checkpoint set were
  used). Extract spread_home at each observation, in chronological order.
  Compute the sign of each consecutive delta, DROPPING zero-deltas (an
  unchanged price contributes no directional information to reverse).
  label = 1 if the resulting sign sequence contains at least one sign
  change (e.g. +,+,-  -- the line moved one way, then reversed).
  label = 0 if the sign sequence is monotonic (all same sign).
  label = None if fewer than 2 real (non-zero) deltas exist -- there's
  nothing to compare for a reversal.

This is a fact about the observed PATH itself, not a claim about why it
happened.
"""

from datetime import datetime

from validation.targets.base import Target
from reporting.checkpoint_reconstruction import get_book_snapshots, _effective_time
from validation.targets.market_movement import _select_book


def _parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class ResistanceDivergenceTarget(Target):
    name = "market_resistance_divergence"
    target_type = "behavioral"
    evidence_family = "temporal_path"
    metric_family = "multiclass"  # confirmation / resistance / divergence / mixed

    def get_label(self, conn, canonical_game_id, as_of_cutoff, **kwargs):
        return None  # DELIBERATELY dormant -- requires a public-pressure baseline that doesn't exist yet

    def data_availability_check(self, conn):
        rows = conn.execute(
            "SELECT canonical_game_id, COUNT(*) c FROM market_snapshots "
            "GROUP BY canonical_game_id HAVING c >= 2"
        ).fetchall()
        return len(rows)


class ReversalTarget(Target):
    name = "market_reversal"
    target_type = "behavioral"
    evidence_family = "temporal_path"
    metric_family = "classification"

    def get_label(self, conn, canonical_game_id, as_of_cutoff, **kwargs):
        book = _select_book(conn, canonical_game_id, kwargs.get("book"))
        if book is None:
            return None

        kickoff = _parse_iso(as_of_cutoff)
        snapshots = get_book_snapshots(conn, canonical_game_id, book)
        pregame = [s for s in snapshots if _parse_iso(_effective_time(s)) < kickoff]
        pregame.sort(key=lambda s: _effective_time(s))

        spreads = [s["spread_home"] for s in pregame if s["spread_home"] is not None]
        if len(spreads) < 2:
            return None

        signs = []
        for a, b in zip(spreads, spreads[1:]):
            delta = b - a
            if delta != 0:
                signs.append(1 if delta > 0 else -1)

        if len(signs) < 2:
            return None  # fewer than 2 real transitions -- nothing to compare for a reversal

        for s1, s2 in zip(signs, signs[1:]):
            if s1 != s2:
                return 1
        return 0

    def data_availability_check(self, conn):
        rows = conn.execute(
            "SELECT canonical_game_id, COUNT(*) c FROM market_snapshots "
            "GROUP BY canonical_game_id HAVING c >= 3"
        ).fetchall()
        return len(rows)
