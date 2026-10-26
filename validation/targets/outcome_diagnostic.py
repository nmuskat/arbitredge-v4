"""
validation/targets/outcome_diagnostic.py

The ONLY target usable against the 2025 cohort today. Explicitly
target_type='diagnostic' -- exists purely to exercise the harness
mechanically. Every result this target produces carries NON_BEHAVIORAL_WARNING
verbatim through to the experiment log and any report output.
"""

from validation.targets.base import Target, NON_BEHAVIORAL_WARNING  # noqa: F401


class OutcomeDiagnosticTarget(Target):
    name = "game_outcome_diagnostic"
    target_type = "diagnostic"
    evidence_family = None
    metric_family = "classification"

    def get_label(self, conn, canonical_game_id, as_of_cutoff=None, **kwargs):
        row = conn.execute(
            "SELECT winner_home FROM game_results WHERE canonical_game_id = ? "
            "ORDER BY retrieved_at DESC LIMIT 1",
            (canonical_game_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None  # no result, or a tie -- not a usable binary label, never fabricated
        return int(row[0])

    def data_availability_check(self, conn):
        return conn.execute(
            "SELECT COUNT(DISTINCT canonical_game_id) FROM game_results WHERE winner_home IS NOT NULL"
        ).fetchone()[0]
