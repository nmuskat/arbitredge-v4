"""
operations/cadence_config.py

Cadence configuration only -- no collection logic here. Odds cadence is
game-proximity-aware, per this round's revision. Context/results/backup
cadence are unchanged from the previously approved day-based design.

ODDS_PROXIMITY_TIERS: (label, max_hours_before_kickoff, min_hours_before_kickoff,
interval_minutes). max=None is the catch-all "far out or nothing tracked
yet" tier -- this also serves as the DISCOVERY cadence: the-odds-api won't
tell us about next week's slate until we actually poll, so even with zero
tracked near-term games we still poll once/day to discover what's coming.

REVISED (real operational audit against the confirmed 2026 September
schedule): the "elevated" tier interval was widened from 300min (5h) to
480min (8h). A discrete-event simulation using the real production
tier_for_hours function, driven by the ACTUAL confirmed 2026 NFL Week 2
kickoff clusters (6 distinct times) and CFB's real September Saturday
kickoff density (14 distinct clusters, cross-checked against a real
NCAA.com listing -- CFB genuinely spreads across far more distinct
kickoff windows than NFL, which the tiers' earlier ~404/month estimate
appears to have undercounted) showed only a 6-credit monthly safety
margin at the original 5h elevated interval -- real, uncomfortably thin
risk, not a theoretical one. 74% of weekly credits were found concentrated
in the high/highest tiers (the dense final-6h/final-1h windows), meaning
that value is genuine, not waste -- so the fix widens ONLY the elevated
tier (24h-6h out), which contributed disproportionate cost for
comparatively low research value. high/highest are UNCHANGED. This
recovers a 31-credit margin (469/month projected) without touching final-
hour density at all.
"""

ODDS_PROXIMITY_TIERS = [
    ("very_low",     None, 72, 1440),  # >72h out, or no tracked future game: once/day (also discovery)
    ("low_moderate", 72,   24, 960),   # 72h-24h out: every 16h
    ("elevated",     24,   6,  480),   # 24h-6h out: every 8h (widened from 5h -- see module docstring)
    ("high",         6,    1,  60),    # 6h-1h out: every 1h -- UNCHANGED
    ("highest",      1,    0,  20),    # <1h out: every 20min -- UNCHANGED
]

CONTEXT_POLL_INTERVAL_MINUTES_NORMAL = 24 * 60
CONTEXT_POLL_INTERVAL_MINUTES_GAMEDAY = 12 * 60

RESULTS_POLL_INTERVAL_MINUTES = 180

BACKUP_INTERVAL_MINUTES = 24 * 60


def tier_for_hours(hours_to_nearest_kickoff):
    """Returns (tier_label, required_interval_minutes) for a given
    hours-to-nearest-tracked-kickoff value. None means no tracked future
    game exists for this sport -- treated as the very_low/discovery tier,
    since that tier's daily poll is exactly what eventually discovers the
    next game."""
    if hours_to_nearest_kickoff is None:
        return ODDS_PROXIMITY_TIERS[0][0], ODDS_PROXIMITY_TIERS[0][3]
    for label, mx, mn, interval in ODDS_PROXIMITY_TIERS:
        if (mx is None or hours_to_nearest_kickoff <= mx) and hours_to_nearest_kickoff > mn:
            return label, interval
    return ODDS_PROXIMITY_TIERS[-1][0], ODDS_PROXIMITY_TIERS[-1][3]
