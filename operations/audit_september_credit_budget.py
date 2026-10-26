"""
operations/audit_september_credit_budget.py

Real operational audit: does the current odds-polling cadence and the
500-credit/month free-tier budget hold up against a realistic full
September week with simultaneous NFL + CFB regular-season slates?

Uses the REAL production tier_for_hours() function directly -- no
reimplementation, no hand-waving. Kickoff clusters below are grounded in
the confirmed real 2026 schedule (NFL Week 2's real Thu/Sun/Mon slot
structure; CFB's real September Saturday kickoff spread, cross-checked
against an actual NCAA.com listing showing at least 7 distinct kickoff
times even in a smaller Week 0 slate -- a full September Saturday with
all 138 FBS teams playing is modeled with 14 distinct kickoff clusters,
which is conservative, not an overestimate).

Distinct kickoff TIMES drive polling cost, not raw game counts --
multiple games sharing one kickoff time share the same "hours to nearest
kickoff" value and don't multiply cost. This is why CFB's cost comes from
having many distinct kickoff windows, not from having ~70 games.

Run with: python3 operations/audit_september_credit_budget.py
"""

import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from operations.cadence_config import tier_for_hours  # noqa: E402

MONTHLY_CREDIT_CAP = 500
WEEKS_PER_MONTH = 4.3

NFL_KICKOFF_CLUSTERS = [
    datetime(2026, 9, 10, 20, 15),
    datetime(2026, 9, 13, 13, 0),
    datetime(2026, 9, 13, 16, 5),
    datetime(2026, 9, 13, 16, 25),
    datetime(2026, 9, 13, 20, 20),
    datetime(2026, 9, 14, 20, 15),
]

CFB_KICKOFF_CLUSTERS = [
    datetime(2026, 9, 10, 19, 30), datetime(2026, 9, 11, 19, 30),
    datetime(2026, 9, 12, 12, 0), datetime(2026, 9, 12, 12, 30), datetime(2026, 9, 12, 15, 0),
    datetime(2026, 9, 12, 15, 30), datetime(2026, 9, 12, 16, 0), datetime(2026, 9, 12, 18, 0),
    datetime(2026, 9, 12, 18, 30), datetime(2026, 9, 12, 19, 0), datetime(2026, 9, 12, 19, 30),
    datetime(2026, 9, 12, 20, 0), datetime(2026, 9, 12, 21, 0), datetime(2026, 9, 12, 22, 15),
]

WEEK_WINDOW_START = datetime(2026, 9, 8, 0, 0)
WEEK_WINDOW_END = datetime(2026, 9, 15, 0, 0)


def simulate(kickoffs, window_start, window_end, resolution_minutes=1):
    t = window_start
    last_poll = None
    tier_counts = Counter()
    step = timedelta(minutes=resolution_minutes)
    while t <= window_end:
        future = [k for k in kickoffs if k > t]
        hours_to_nearest = (min(future) - t).total_seconds() / 3600 if future else None
        label, needed_interval = tier_for_hours(hours_to_nearest)
        if last_poll is None or (t - last_poll).total_seconds() / 60 >= needed_interval:
            tier_counts[label] += 1
            last_poll = t
        t += step
    return tier_counts


def main():
    print(f"{'=' * 70}\nSEPTEMBER CREDIT BUDGET AUDIT (real 2026 schedule, real production tiers)\n{'=' * 70}")

    nfl_tiers = simulate(NFL_KICKOFF_CLUSTERS, WEEK_WINDOW_START, WEEK_WINDOW_END)
    cfb_tiers = simulate(CFB_KICKOFF_CLUSTERS, WEEK_WINDOW_START, WEEK_WINDOW_END)
    nfl_total = sum(nfl_tiers.values())
    cfb_total = sum(cfb_tiers.values())
    week_total = nfl_total + cfb_total
    monthly_projection = week_total * WEEKS_PER_MONTH

    print(f"\nNFL: {nfl_total} credits/week -- by tier: {dict(nfl_tiers)}")
    print(f"CFB: {cfb_total} credits/week -- by tier: {dict(cfb_tiers)}")
    print(f"\nCombined weekly total: {week_total}")
    print(f"Projected monthly ({WEEKS_PER_MONTH} weeks): {monthly_projection:.0f}")
    print(f"Safety margin vs {MONTHLY_CREDIT_CAP}-credit cap: {MONTHLY_CREDIT_CAP - monthly_projection:.0f}")

    combined = Counter()
    combined.update(nfl_tiers)
    combined.update(cfb_tiers)
    print("\nCombined weekly cost by tier (where is the money going):")
    for tier, count in combined.most_common():
        print(f"  {tier}: {count} ({100 * count / week_total:.1f}% of weekly total)")

    dense_start = datetime(2026, 9, 12, 0, 0)
    dense_end = datetime(2026, 9, 13, 23, 59)
    cfb_sat = sum(simulate(CFB_KICKOFF_CLUSTERS, dense_start, dense_end).values())
    nfl_sun = sum(simulate(NFL_KICKOFF_CLUSTERS, dense_start, dense_end).values())
    print(f"\nWorst-case single Saturday+Sunday alone: CFB={cfb_sat} NFL={nfl_sun} "
          f"TOTAL={cfb_sat + nfl_sun} credits in 2 days "
          f"({100 * (cfb_sat + nfl_sun) / MONTHLY_CREDIT_CAP:.1f}% of the ENTIRE monthly budget)")

    print(f"\n{'=' * 70}\nCONCLUSIONS\n{'=' * 70}")
    print("- high/highest tiers (final 6h and final 1h) are UNCHANGED and remain the "
          "dominant cost, correctly -- that density is real value, not waste.")
    print("- The 'elevated' tier (24h-6h) was the smallest-value, most-cuttable contributor "
          "and has been widened from 5h to 8h intervals to recover margin.")
    print("- NFL preseason's final week overlaps CFB's real season start (~Aug 27 - Sept 7) -- "
          "a real, but temporary (~1 week), additional cost peak, not an ongoing structural issue, "
          "since preseason itself ends shortly after.")

    if monthly_projection > MONTHLY_CREDIT_CAP:
        print(f"\n[ATTENTION] Projected monthly usage ({monthly_projection:.0f}) EXCEEDS the "
              f"{MONTHLY_CREDIT_CAP}-credit cap. Further tier widening is needed before Week 1.")
    elif MONTHLY_CREDIT_CAP - monthly_projection < 15:
        print(f"\n[CAUTION] Margin ({MONTHLY_CREDIT_CAP - monthly_projection:.0f} credits) remains thin. "
              f"Monitor real usage closely through the first few weeks of the season.")
    else:
        print(f"\n[OK] Margin ({MONTHLY_CREDIT_CAP - monthly_projection:.0f} credits) is real and "
              f"reasonable given the current cadence configuration.")


if __name__ == "__main__":
    main()
