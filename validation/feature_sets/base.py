"""
validation/feature_sets/base.py

FeatureSet interface. Rung 0 and Rung 1 both read from the already-built,
already-validated engineered_features table (different column subsets) --
no new feature computation happens in this package. Future rungs (0.5,
2, 3, 4) register here later as they're each separately designed and
approved.
"""

from abc import ABC, abstractmethod


class FeatureSet(ABC):
    rung_name = None

    @abstractmethod
    def get_features(self, conn, canonical_game_id, as_of_cutoff):
        """Returns a dict of {column_name: value} for this game, or None if
        no engineered_features row exists. Does NOT impute or fabricate
        missing values -- a None value inside the dict for an individual
        column is preserved as-is; the harness's ablation step decides how
        to handle incomplete rows (complete-case only, per instruction not
        to impute)."""
        raise NotImplementedError

    @abstractmethod
    def point_in_time_cutoff_used(self, conn, canonical_game_id):
        """Returns the point_in_time_cutoff actually stored for this game's
        engineered_features row, so the harness can cross-check it against
        the target's own timestamp before trusting a comparison."""
        raise NotImplementedError
