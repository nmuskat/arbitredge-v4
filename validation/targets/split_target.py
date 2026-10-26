"""
validation/targets/split_target.py

DORMANT, INTERFACE-ONLY. No legitimate automated split source currently
exists (see the project's Split Source Access Audit). This class exists
solely to prove the Target interface accommodates a future split-based
target without redesigning the harness -- it does not assume a split
source will ever be added.
"""

from validation.targets.base import Target


class SplitLabelTarget(Target):
    name = "observed_split_label"
    target_type = "behavioral"
    evidence_family = "split_label"
    metric_family = "regression"  # e.g. ticket% or handle% for a chosen side

    def get_label(self, conn, canonical_game_id, as_of_cutoff, **kwargs):
        return None  # not yet implemented -- no legitimate split source exists yet

    def data_availability_check(self, conn):
        return conn.execute("SELECT COUNT(DISTINCT canonical_game_id) FROM split_observations").fetchone()[0]
