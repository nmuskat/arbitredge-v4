"""
splits_v4/slates.py -- when to take a splits snapshot.

Owner 2026-11-13: run for every NFL game and every AP Top-25 college game, any day of the week, starting about
2 hours before game time -- independent of the odds-collection schedule. The splits workflow checks every hour
(08:05-00:05 ET) and takes ONE snapshot per slate when the slate's first kickoff is 60-120 min away (an hourly check
always lands one run in that window). A slate first seen later than that (under 60 min, before kickoff) is taken
late and labelled LATE. A slate = kickoffs within SLATE_GAP_S of its first kickoff.
"""
SLATE_GAP_S = 100 * 60
DUE_MIN_S, DUE_MAX_S = 60 * 60, 120 * 60


def slates(kickoffs):
    """[slate start epoch] from kickoff epochs."""
    out = []
    for k in sorted(set(kickoffs)):
        if not out or k - out[-1] > SLATE_GAP_S:
            out.append(k)
    return out


def due(now, kickoffs, done):
    """(slate_start, 'ON TIME'|'LATE') for the next slate that should be captured now, or (None, reason)."""
    taken = {int(x) for x in done}
    for s in slates(kickoffs):
        if s <= now or int(s) in taken:
            continue
        left = s - now
        if DUE_MIN_S <= left <= DUE_MAX_S:
            return s, "ON TIME"
        if 0 < left < DUE_MIN_S:
            return s, "LATE"
        return None, f"next slate in {int(left // 60)} min"
    return None, "no upcoming slate"


def members(events, slate_start):
    """events [[event_id, kickoff, home, away], ...] belonging to the slate starting at slate_start."""
    return [e for e in events if slate_start <= e[1] <= slate_start + SLATE_GAP_S]


def tracked_kickoffs(state, rankings_snapshots, now):
    """Kickoffs that get a snapshot: every NFL game; AP Top-25 college games (status unknown kept), from the
    collector's free event list (collect_v4.schedule_policy.priority_kickoffs, the same filter collection uses)."""
    from collect_v4 import schedule_policy as SP
    out = []
    for sport in ("americanfootball_nfl", "americanfootball_ncaaf"):
        ev = (state.get(sport) or {}).get("events", [])
        out += SP.priority_kickoffs(sport, ev, rankings_snapshots, now)[0]
    return out
