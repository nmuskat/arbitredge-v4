"""
validation/targets/market_clv.py

DORMANT. Closing-line value requires a defined closing-market reference
(unresolved design question from earlier research) plus real pregame
market_snapshots -- the 2025 cohort has neither. evidence_family is set to
'magnitude' as a provisional classification (CLV is fundamentally a price
response measure); per instruction not to freeze the family taxonomy
prematurely, this can be revisited once real CLV computation is designed.
"""

from validation.targets.base import Target


class CLVTarget(Target):
    name = "closing_line_value"
    target_type = "behavioral"
    evidence_family = "magnitude"
    metric_family = "regression"

    def get_label(self, conn, canonical_game_id, as_of_cutoff, **kwargs):
        return None  # not yet implemented -- dormant; closing-market definition also unresolved

    def data_availability_check(self, conn):
        rows = conn.execute(
            "SELECT canonical_game_id, COUNT(*) c FROM market_snapshots "
            "GROUP BY canonical_game_id HAVING c >= 2"
        ).fetchall()
        return len(rows)
