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


def cron_strings():
    out = []
    for mins, hours, days in CRONS:
        out.append(f"{','.join(map(str, mins))} {','.join(map(str, hours))} * * {','.join(map(str, days))}")
    return out


def run_times(week_start_epoch):
    """Every scheduled run in the 7 days from week_start (a Sunday 00:00 UTC)."""
    times = set()
    for day in range(7):
        dow = day % 7            # week_start is Sunday -> dow 0
        for mins, hours, days in CRONS:
            if dow in days:
                for h in hours:
                    for m in mins:
                        times.add(week_start_epoch + day * 86400 + h * 3600 + m * 60)
    return sorted(times)
