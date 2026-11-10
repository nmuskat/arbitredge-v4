"""
collect_v4/backfill.py -- one-time historical odds backfill (owner-approved 2026-11-10).

Fills the weeks before live collection started (live book weeks begin 2026-10-27 12:00 UTC) with
historical odds snapshots from The Odds API, so the synthetic book can be replayed on the whole
season. Paid calls go through the SAME in-process gateway and credit gate as live collection
(collect_v4.actions_run): provider-balance preflight before every paid request, V3 floor, per-request
ceiling, breaker, append-only ledger.

Budget: a SEPARATE one-time allowance (BACKFILL_HARD_MAX, expires BACKFILL_EXPIRES). The live monthly
cap and its 6,000 ceiling are unchanged; live collection's own allowance is not reduced by backfill
spending (actions_run.caps_from_env adds back historical spend). The owner sets the allowance actually
usable with repository variable V4_BACKFILL_BUDGET (clamped to BACKFILL_HARD_MAX).

Plan per sport and betting week (Tuesday 12:00 UTC to Tuesday, same windows as the live book):
  * one ANCHOR snapshot Wednesday 16:00 UTC: it lists the week's games and kickoffs;
  * a snapshot at 16:00 UTC on each later day up to the last game day;
  * for every kickoff cluster (games within 30 min): kickoff - 3 h, - 1 h and - 10 min
    (the - 1 h snapshot is where a final pick is made; the - 10 min one is the observed close);
  * CFB clusters only for AP Top-25 games (the book's universe), by the poll that applied that week;
  * the pick and close snapshots are always kept; optional ones closer than 20 min to a kept one are dropped.
Each snapshot is stored with fetched_at = the provider's snapshot timestamp, so the replay sees the
market exactly as it stood then. Output shards go to a SEPARATE release (odds-hist), never into the
live odds-data-* releases, so the live site never reads them.
"""

import calendar
import json
import os
import time

BACKFILL_HARD_MAX = 12_000                 # one-time allowance ceiling (credits), owner-approved 2026-11-10
BACKFILL_START = "2026-11-10T00:00:00Z"    # historical spend from here on counts against the allowance
BACKFILL_EXPIRES = "2026-12-01T00:00:00Z"  # after this no backfill request is authorised
HIST_MARKETS = ("h2h", "spreads", "totals")  # same markets as live: 10 x 3 = 30 credits per snapshot
FIRST_WINDOW = {"nfl": "2026-09-08T12:00:00Z", "cfb": "2026-08-25T12:00:00Z"}
LIVE_FROM = "2026-10-27T12:00:00Z"         # first live-book week: everything before it is backfilled
SPORT_KEYS = {"nfl": "americanfootball_nfl", "cfb": "americanfootball_ncaaf"}
CLUSTER_GAP_S = 30 * 60
PRE_KICK_S = (3 * 3600, 3600, 600)
MIN_SPACING_S = 20 * 60
ANCHOR_HOUR_UTC = 16
CFB_WEEK1_WINDOW = "2026-09-01T12:00:00Z"  # CFB week 1 (AP poll week k applies to window week1 + 7(k-1))


def epoch(iso):
    return calendar.timegm(time.strptime(iso.replace("Z", ""), "%Y-%m-%dT%H:%M:%S"))


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def windows(sport, live_from=LIVE_FROM):
    """Betting-week windows (start, end) to backfill for a sport, oldest first."""
    out, ws, stop = [], epoch(FIRST_WINDOW[sport]), epoch(live_from)
    while ws < stop:
        out.append((ws, ws + 7 * 86400))
        ws += 7 * 86400
    return out


def anchor_time(ws):
    """Wednesday 16:00 UTC of the window (ws is Tuesday 12:00)."""
    return ws + 86400 - 12 * 3600 + ANCHOR_HOUR_UTC * 3600


def clusters(kickoffs, gap=CLUSTER_GAP_S):
    """[(first, last)] kickoff clusters: a new cluster starts when a kickoff is > gap after the previous one."""
    out = []
    for k in sorted(set(kickoffs)):
        if out and k - out[-1][1] <= gap:
            out[-1][1] = k
        else:
            out.append([k, k])
    return [tuple(c) for c in out]


def plan_times(ws, we, kickoffs, now):
    """Snapshot times for one week after the anchor (sorted, deduplicated, strictly before `now`).
    Essential times (each cluster's - 1 h pick snapshot and - 10 min close) are always kept (only exact
    near-duplicates within 5 min merge); optional ones (- 3 h, daily 16:00 UTC) only when at least
    MIN_SPACING_S from everything kept."""
    ks = [k for k in kickoffs if ws <= k < we]
    if not ks:
        return []
    lo = anchor_time(ws) + MIN_SPACING_S
    essential, optional = set(), set()
    day = anchor_time(ws) + 86400
    while day < max(ks):
        optional.add(day)
        day += 86400
    for first, last in clusters(ks):
        optional.add(first - PRE_KICK_S[0])
        essential.update({first - PRE_KICK_S[1], first - PRE_KICK_S[2]})
        if last - first > 15 * 60:
            essential.add(last - PRE_KICK_S[2])
    kept = []
    for t in sorted((t for t in essential if lo <= t < now), reverse=True):
        if not kept or kept[-1] - t >= 5 * 60:
            kept.append(t)
    for t in sorted(t for t in optional if lo <= t < now):
        if all(abs(t - k) >= MIN_SPACING_S for k in kept):
            kept.append(t)
    return sorted(kept)


def poll_snapshots(rankings_snapshots):
    """Per-week AP snapshots for the replay: poll week k as if fetched at the start of CFB week k's window
    (Tuesday after the Sunday release); the preseason poll (week 1) also covers week 0. Polls are historical
    facts released before the games they rank."""
    snaps = [s for s in rankings_snapshots or [] if s.get("polls")]
    if not snaps:
        return []
    latest = max(snaps, key=lambda s: s["fetched_at"])
    out = []
    w1 = epoch(CFB_WEEK1_WINDOW)
    for p in latest["polls"]:
        if p.get("season_type", "regular") != "regular" or not p.get("week"):
            continue
        ats = [w1 + 7 * 86400 * (p["week"] - 1)]
        if p["week"] == 1:
            ats.insert(0, w1 - 7 * 86400)          # the preseason poll also ranks week 0
        for at in ats:
            out.append({"fetched_at": at, "polls": [p], "teams": latest.get("teams") or [],
                        "source": latest.get("source"), "year": latest.get("year")})
    return sorted(out, key=lambda s: s["fetched_at"])


def top25_filter(games, polls):
    """Keep CFB games with a ranked team under the poll that applied at kickoff."""
    from data_v4 import rankings
    keep = []
    for g in games:
        st = rankings.top25_status(g["home"], g["away"], g["kickoff"], polls)
        if st.get("status") == "OK" and st.get("top25"):
            keep.append(g)
    return keep


def estimate(sport, kickoffs_by_window, now):
    """{window_start_iso: n_snapshots} including the anchor."""
    return {iso(ws): 1 + len(plan_times(ws, we, kickoffs_by_window.get(ws, []), now))
            for ws, we in windows(sport)}


# ------------------------------------------------------------------ progress state
def load_state(path):
    return json.load(open(path)) if os.path.exists(path) else {"done": {}, "weeks": {}}


def save_state(state, path):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, path)
