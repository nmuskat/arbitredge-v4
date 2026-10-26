"""
validation/targets/cross_book.py

CrossBookDisagreementTarget: reuses checkpoint_reconstruction.py's
already-tested compute_cross_book_disagreement directly -- no separate
market-path definition duplicated here.

DEFINITION: at a given named checkpoint (default: last_observed_before_kickoff
-- the checkpoint closest to the real decision point), the range
(max - min) of spread_home across every book with a valid observation at
that checkpoint. This remains a DESCRIPTIVE MARKET-STATE MEASUREMENT
(requirement 7) -- a plain fact about how much books disagree -- never
labeled or treated as a "sharp signal." No book is treated as more
authoritative than another; every book's real value is preserved as-is
(no consensus computed, matching the standing "no consensus logic yet"
instruction). label = None when fewer than 2 books have a valid
observation at the requested checkpoint.
"""

from validation.targets.base import Target
from reporting.checkpoint_reconstruction import compute_cross_book_disagreement, ALL_CHECKPOINT_NAMES

DEFAULT_CHECKPOINT = "last_observed_before_kickoff"


class CrossBookDisagreementTarget(Target):
    name = "cross_book_disagreement"
    target_type = "behavioral"
    evidence_family = "cross_book"
    metric_family = "regression"

    def get_label(self, conn, canonical_game_id, as_of_cutoff, **kwargs):
        checkpoint_name = kwargs.get("checkpoint_name", DEFAULT_CHECKPOINT)
        if checkpoint_name not in ALL_CHECKPOINT_NAMES:
            raise ValueError(f"checkpoint_name must be one of {ALL_CHECKPOINT_NAMES}, got {checkpoint_name!r}")
        disagreement = compute_cross_book_disagreement(conn, canonical_game_id, as_of_cutoff)
        return disagreement[checkpoint_name]["range"]

    def data_availability_check(self, conn):
        rows = conn.execute(
            "SELECT canonical_game_id, COUNT(DISTINCT book) c FROM market_snapshots "
            "GROUP BY canonical_game_id HAVING c >= 2"
        ).fetchall()
        return len(rows)
