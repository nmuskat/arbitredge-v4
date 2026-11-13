"""
splits_v4/slates.py -- when to take a splits snapshot (owner's plan 2026-11-12): once per slate, about 1 hour before
the slate's first kickoff. A slate = kickoffs that start within SLATE_GAP_S of the slate's first kickoff
(Sunday 1:00 / 4:05-4:25 / SNF are three slates; a Saturday's CFB windows each a slate).

Called by the collection run (no network, no API calls): it already knows every kickoff for free. Due when the first
kickoff is 50-90 min away (widened from 75 on 2026-11-13: GitHub started runs late on the first live night);
a slate first seen later than that (under 50 min, still before kickoff) is taken late and labelled LATE.
"""
SLATE_GAP_S = 100 * 60
DUE_MIN_S, DUE_MAX_S = 50 * 60, 90 * 60     # 90 min: owner 2026-11-13 (GitHub can start scheduled runs late)


def slates(kickoffs):
    """[slate start epoch] from kickoff epochs."""
    out = []
    for k in sorted(set(kickoffs)):
        if not out or k - out[-1] > SLATE_GAP_S:
            out.append(k)
    return out


def due(now, kickoffs, done):
    """(slate_start, 'ON TIME'|'LATE') for the next slate that should be captured now, or (None, reason)."""
    for s in slates(kickoffs):
        if s <= now or int(s) in {int(x) for x in done}:
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
