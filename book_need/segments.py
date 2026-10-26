"""
book_need/segments.py

Segment resolution (NOT_YET_OBSERVED / COMPLETE / IN_PROGRESS) and the
final corrected per-segment evaluation_horizon staleness rule: staleness
is always judged at the SEGMENT's own evaluation_horizon
(min(segment_end, as_of)), never globally at as_of. This is what makes a
COMPLETE historical segment immune to a later, denser cadence
retroactively invalidating it.
"""

from features.market_state_as_of import resolve_named_cutoff, _parse_iso
from operations.cadence_config import tier_for_hours

NOT_YET_OBSERVED = "NOT_YET_OBSERVED"
COMPLETE = "COMPLETE"
IN_PROGRESS = "IN_PROGRESS"

SEGMENT_DEFS = [
    ("A", "T-24h", "T-6h"),
    ("B", "T-6h", "T-1h"),
    ("C", "T-1h", "kickoff"),
]


def resolve_segments(commence_time, as_of):
    as_of_dt = _parse_iso(as_of)
    results = []
    for name, start_label, end_label in SEGMENT_DEFS:
        start = resolve_named_cutoff(commence_time, start_label)
        end = resolve_named_cutoff(commence_time, end_label)
        start_dt = _parse_iso(start)
        end_dt = _parse_iso(end)

        if as_of_dt < start_dt:
            results.append({"name": name, "start": start, "start_label": start_label, "end": end,
                             "status": NOT_YET_OBSERVED, "evaluation_horizon": None})
            continue

        evaluation_horizon_dt = min(end_dt, as_of_dt)

        if evaluation_horizon_dt == start_dt:
            results.append({"name": name, "start": start, "start_label": start_label, "end": end,
                             "status": NOT_YET_OBSERVED, "evaluation_horizon": None})
            continue

        evaluation_horizon = evaluation_horizon_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        status = COMPLETE if evaluation_horizon_dt >= end_dt else IN_PROGRESS
        results.append({"name": name, "start": start, "start_label": start_label, "end": end,
                         "status": status, "evaluation_horizon": evaluation_horizon})
    return results


def is_stale(commence_time, evaluation_horizon, last_observation_effective_time):
    if last_observation_effective_time is None:
        return True
    horizon_dt = _parse_iso(evaluation_horizon)
    commence_dt = _parse_iso(commence_time)
    hours_to_kickoff_at_horizon = (commence_dt - horizon_dt).total_seconds() / 3600
    _, required_interval_minutes = tier_for_hours(hours_to_kickoff_at_horizon)
    last_obs_dt = _parse_iso(last_observation_effective_time)
    age_minutes = (horizon_dt - last_obs_dt).total_seconds() / 60
    return age_minutes > required_interval_minutes
