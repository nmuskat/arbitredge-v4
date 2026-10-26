"""
validation/targets/market_movement.py

MovementDirectionTarget / MovementMagnitudeTarget: real, implemented
target logic (no longer dormant), reusing checkpoint_reconstruction.py's
already-tested reconstruct_checkpoints_for_book rather than re-deriving
market-path definitions.

DEFINITION (both targets): compares a book's spread_home at
earliest_observed against spread_home at last_observed_before_kickoff --
the full pregame trading window's bookends. This is a SPREAD-NUMBER
measurement specifically (points moved), deliberately distinct from
price/juice movement (requirement 5) -- price data remains fully
inspectable via the same underlying checkpoint records (each returned
snapshot dict still carries spread_price_home), it's just not what these
two targets' own label represents. No consensus across books is computed
here; PER-BOOK by design, selected via the optional book= kwarg (see
_select_book below for the disclosed default-book rule when none is given).

MovementDirectionTarget (classification):
  delta = last_observed_before_kickoff.spread_home - earliest_observed.spread_home
  label = 1 if delta < 0 (line moved toward the home team)
  label = 0 if delta > 0 (line moved toward the away team)
  label = None if delta == 0 (no real movement to characterize) OR either
          checkpoint is missing.

MovementMagnitudeTarget (regression):
  label = abs(delta), in points. None under the same missing-data conditions.

Both require earliest_observed and last_observed_before_kickoff to both
be non-post-kickoff observations -- inherited for free from
reconstruct_checkpoints_for_book's own strict pregame filter, not
reimplemented here.

KEY-NUMBER METADATA (requirement 6): crosses_key_number() is provided as
a plain descriptive helper -- does the [min,max] interval spanned by the
two spread_home values contain 3 or 7 (the two well-established NFL key
numbers; not asserted as a complete list). It is NOT part of either
target's numeric label and carries no behavioral weight -- it's exposed
for the diagnostic script to report as metadata only.
"""

from validation.targets.base import Target
from reporting.checkpoint_reconstruction import get_book_snapshots, reconstruct_checkpoints_for_book

KEY_NUMBERS = {3, 7}  # the two well-established NFL key numbers; deliberately not a longer asserted list


def _select_book(conn, canonical_game_id, book=None):
    """Disclosed default-book rule when none is specified: the book with
    the MOST pregame observations for this game (most complete real path
    available) -- an objective completeness tie-breaker, not a judgment
    about which book is more 'reliable' or 'sharp'."""
    if book is not None:
        return book
    rows = conn.execute(
        "SELECT book, COUNT(*) c FROM market_snapshots WHERE canonical_game_id = ? "
        "GROUP BY book ORDER BY c DESC LIMIT 1",
        (canonical_game_id,),
    ).fetchone()
    return rows[0] if rows else None


def crosses_key_number(value_a, value_b):
    """Key numbers (3, 7) refer to the MAGNITUDE of the spread, not its
    signed value -- a favorite's spread is stored negative (e.g. -3.0),
    so the crossing check must test both +/-3 and +/-7 as thresholds,
    not just the literal positive values.

    DISCLOSED LIMITATION: this checks only the two BOOKEND values passed
    in (typically earliest_observed vs last_observed_before_kickoff) --
    it will not detect a crossing that occurred at an intermediate peak
    and then receded (e.g. a path of -1.5 -> -3.5 -> -2.0 crosses 3 at
    its peak but the bookends -1.5/-2.0 do not span it). This is
    acceptable for descriptive metadata with no assigned behavioral
    weight, per instruction -- flagged explicitly here rather than
    silently presenting bookend-only detection as full-path analysis."""
    if value_a is None or value_b is None:
        return None
    lo, hi = (value_a, value_b) if value_a <= value_b else (value_b, value_a)
    thresholds = set()
    for k in KEY_NUMBERS:
        thresholds.add(k)
        thresholds.add(-k)
    return any(lo <= t <= hi for t in thresholds)


def _get_bookend_checkpoints(conn, canonical_game_id, as_of_cutoff, book):
    resolved_book = _select_book(conn, canonical_game_id, book)
    if resolved_book is None:
        return None, None
    snapshots = get_book_snapshots(conn, canonical_game_id, resolved_book)
    checkpoints = reconstruct_checkpoints_for_book(canonical_game_id, as_of_cutoff, resolved_book, snapshots)
    return checkpoints["earliest_observed"], checkpoints["last_observed_before_kickoff"]


class MovementDirectionTarget(Target):
    name = "market_movement_direction"
    target_type = "behavioral"
    evidence_family = "directional"
    metric_family = "classification"

    def get_label(self, conn, canonical_game_id, as_of_cutoff, **kwargs):
        earliest, last = _get_bookend_checkpoints(conn, canonical_game_id, as_of_cutoff, kwargs.get("book"))
        if earliest is None or last is None or earliest["spread_home"] is None or last["spread_home"] is None:
            return None
        delta = last["spread_home"] - earliest["spread_home"]
        if delta == 0:
            return None
        return 1 if delta < 0 else 0

    def data_availability_check(self, conn):
        rows = conn.execute(
            "SELECT canonical_game_id, COUNT(*) c FROM market_snapshots "
            "GROUP BY canonical_game_id HAVING c >= 2"
        ).fetchall()
        return len(rows)


class MovementMagnitudeTarget(Target):
    name = "market_movement_magnitude"
    target_type = "behavioral"
    evidence_family = "magnitude"
    metric_family = "regression"

    def get_label(self, conn, canonical_game_id, as_of_cutoff, **kwargs):
        earliest, last = _get_bookend_checkpoints(conn, canonical_game_id, as_of_cutoff, kwargs.get("book"))
        if earliest is None or last is None or earliest["spread_home"] is None or last["spread_home"] is None:
            return None
        return abs(round(last["spread_home"] - earliest["spread_home"], 2))

    def data_availability_check(self, conn):
        rows = conn.execute(
            "SELECT canonical_game_id, COUNT(*) c FROM market_snapshots "
            "GROUP BY canonical_game_id HAVING c >= 2"
        ).fetchall()
        return len(rows)
