"""
validation/targets/base.py

Target interface. Every target -- present (diagnostic) or future
(behavioral: market movement, resistance, CLV, cross-book, a future split
source) -- implements this exact contract, which is what lets new targets
plug into the harness without touching splitting/ablation/FDR code.
"""

from abc import ABC, abstractmethod

NON_BEHAVIORAL_WARNING = (
    "DIAGNOSTIC / SOFTWARE VALIDATION ONLY -- "
    "NOT EVIDENCE OF PUBLIC PRESSURE -- "
    "NOT EVIDENCE OF BOOK NEED"
)

VALID_TARGET_TYPES = {"diagnostic", "behavioral"}
VALID_METRIC_FAMILIES = {"classification", "regression", "multiclass"}

# Evidence families are intentionally NOT a hard schema-level enum (per
# instruction: do not freeze the exact grouping). This is the current
# Python-side registry, extendable without a migration. Correlated
# transformations of the same underlying market process share a family so
# the harness can later require corroboration ACROSS families, not just
# multiple targets within one family.
EVIDENCE_FAMILIES = {
    "directional": "Directional market response (movement direction, side of movement)",
    "magnitude": "Movement magnitude / price response (points moved, price-only movement)",
    "temporal_path": "Temporal/path behavior (resistance, divergence, reversal, timing)",
    "cross_book": "Cross-book confirmation/disagreement",
    "split_label": "Observed public split labels, if a legitimate source is ever available",
}


class Target(ABC):
    name = None
    target_type = None       # 'diagnostic' | 'behavioral'
    evidence_family = None   # None for diagnostic; must be a key in EVIDENCE_FAMILIES for behavioral
    metric_family = None     # 'classification' | 'regression' | 'multiclass'

    def __init__(self):
        if self.target_type not in VALID_TARGET_TYPES:
            raise ValueError(f"{self.name}: target_type must be one of {VALID_TARGET_TYPES}")
        if self.metric_family not in VALID_METRIC_FAMILIES:
            raise ValueError(f"{self.name}: metric_family must be one of {VALID_METRIC_FAMILIES}")
        if self.target_type == "behavioral" and self.evidence_family not in EVIDENCE_FAMILIES:
            raise ValueError(
                f"{self.name}: behavioral targets must declare a known evidence_family "
                f"(one of {list(EVIDENCE_FAMILIES)}), got {self.evidence_family!r}"
            )
        if self.target_type == "diagnostic" and self.evidence_family is not None:
            raise ValueError(f"{self.name}: diagnostic targets must not declare an evidence_family")

    @abstractmethod
    def get_label(self, conn, canonical_game_id, as_of_cutoff, **kwargs):
        """Returns the label value for this game, or None if unavailable.
        as_of_cutoff exists in the interface for future targets (e.g. CLV)
        whose label depends on a decision timestamp; diagnostic/outcome
        targets ignore it since the outcome doesn't depend on cutoff.
        **kwargs is accepted for forward compatibility with targets that
        need extra, target-specific parameters (e.g. book= for per-book
        market targets, checkpoint_name= for checkpoint-specific targets)
        without forcing every target -- including this one and every
        already-tested dormant stub -- to declare parameters it doesn't
        use. Existing callers that don't pass any kwargs are unaffected."""
        raise NotImplementedError

    @abstractmethod
    def data_availability_check(self, conn):
        """Returns a count of games in the whole database with a usable
        label for this target right now -- used to decide whether a
        behavioral target is still dormant or has enough data to attempt."""
        raise NotImplementedError
