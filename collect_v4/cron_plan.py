"""
collect_v4/cron_plan.py -- the collection workflow's schedule, as data, so the
credit and Actions-minute cost of a week can be simulated and tested before
anything runs. Times are UTC (GitHub cron is UTC). Windows are wide enough to
cover kickoffs under both EDT and EST; minutes avoid :00 (GitHub's busiest).
"""

# (minutes, hours, days-of-week [cron: 0=Sun])
CRONS = [
    ((17,), tuple(range(0, 24, 3)), tuple(range(7))),          # every 3 h: daily / pre-game snapshots
    ((2, 17, 32, 47), (23,), (4,)),                             # Thursday night NFL
    ((2, 17, 32, 47), (0, 1), (5,)),
    ((2, 17, 32, 47), tuple(range(15, 24)), (6,)),              # Saturday CFB (and December NFL Saturdays)
    ((2, 17, 32, 47), (0, 1, 2, 3), (0,)),                      # late Saturday CFB = early Sunday UTC
    ((2, 17, 32, 47), tuple(range(12, 24)), (0,)),              # Sunday NFL incl. international/early kickoffs
    ((2, 17, 32, 47), (0, 1), (1,)),                            # Sunday night NFL
    ((2, 17, 32, 47), (23,), (1,)),                             # Monday night NFL
    ((2, 17, 32, 47), (0, 1), (2,)),
]


# EXPANDED tier only: an hourly run (minute 52) in every hour not already covered by a 15-minute window.
# The workflow SKIPS these runs (0 Actions minutes) unless the owner's V4_TIER is 'expanded'.
def _dense_hours():
    dense = {d: set() for d in range(7)}
    for mins, hours, days in CRONS:
        if len(mins) > 1:
            for d in days:
                dense[d] |= set(hours)
    return dense


QUIET_HOURS_UTC = range(7, 12)      # 2-7 am ET: US books rarely move; the base 3-hourly runs still cover it
EXPANDED_CRONS = [((52,), tuple(h for h in range(24) if h not in _dense_hours()[d] and h not in QUIET_HOURS_UTC),
                   (d,)) for d in range(7)]


def _fmt(crons):
    return [f"{','.join(map(str, m))} {','.join(map(str, h))} * * {','.join(map(str, d))}" for m, h, d in crons]


def expanded_cron_strings():
    return _fmt(EXPANDED_CRONS)


def cron_strings():
    out = []
    for mins, hours, days in CRONS:
        out.append(f"{','.join(map(str, mins))} {','.join(map(str, hours))} * * {','.join(map(str, days))}")
    return out


def run_times(week_start_epoch, tier=None):
    """Every scheduled run in the 7 days from week_start (a Sunday 00:00 UTC) that EXECUTES under the tier
    (the expanded-only runs are skipped by the workflow for every other tier)."""
    times = set()
    crons = CRONS + (EXPANDED_CRONS if tier == "expanded" else [])
    for day in range(7):
        dow = day % 7            # week_start is Sunday -> dow 0
        for mins, hours, days in crons:
            if dow in days:
                for h in hours:
                    for m in mins:
                        times.add(week_start_epoch + day * 86400 + h * 3600 + m * 60)
    return sorted(times)
