"""
features/run_slate_eligibility.py

Pregame eligibility safeguard for the future RUN SLATE system --
STRUCTURALLY SEPARATE from persist_features_v2's retrospective
require_regular_season safeguard (which checks a CONFIRMED
game_results.season_type, unavailable before a game is played). Neither
function calls the other; neither is weakened by this file existing.

AUDIT FINDINGS THIS DESIGN IS BASED ON (confirmed directly against
collectors/odds_collector.py, not assumed):
  1. The literal calling sport parameter ('nfl', 'cfb', 'nfl_preseason')
     is written verbatim into market_snapshots.sport for every row,
     independent of identity resolution.
  2. A canonical_game_id CAN structurally receive snapshots under BOTH
     'nfl'/'cfb' AND 'nfl_preseason' tags -- IDENTITY_SPORT_MAP resolves
     both against the SAME 'nfl' team universe, so if the-odds-api's
     'nfl' and 'nfl_preseason' endpoints ever both returned an entry for
     the same two teams at the same commence_time, both would resolve to
     the same canonical_game_id. Rare in practice, not structurally
     prevented.
  3. Absence of an 'nfl_preseason' tag is NOT sufficient positive
     evidence of regular-season eligibility -- a game with ZERO
     market_snapshots rows (common far from kickoff, or one of the
     permanently-missed games) trivially "lacks the preseason tag"
     without being positively confirmed as anything.

POLICY: FAIL CLOSED. Four possible outcomes, never a silent default to
eligible:
  - ELIGIBLE: real 'nfl'/'cfb' tagged snapshots exist, zero
    'nfl_preseason' tagged snapshots exist for this game.
  - NOT_ELIGIBLE_PRESEASON: any 'nfl_preseason' tagged snapshot exists.
  - AMBIGUOUS_CONFLICTING_TAGS: BOTH 'nfl_preseason' AND 'nfl'/'cfb'
    tagged snapshots exist for the same canonical_game_id.
  - UNKNOWN_INSUFFICIENT_EVIDENCE: zero market_snapshots rows of any kind
    exist yet -- not enough evidence either way.

Only ELIGIBLE should ever be treated as "safe to proceed" by a future
RUN SLATE caller. The other three are all, deliberately, non-eligible.
"""

ELIGIBLE = "ELIGIBLE"
NOT_ELIGIBLE_PRESEASON = "NOT_ELIGIBLE_PRESEASON"
AMBIGUOUS_CONFLICTING_TAGS = "AMBIGUOUS_CONFLICTING_TAGS"
UNKNOWN_INSUFFICIENT_EVIDENCE = "UNKNOWN_INSUFFICIENT_EVIDENCE"


def check_run_slate_eligibility(conn, canonical_game_id):
    """
    Returns a dict: {status, has_preseason_tag, has_regular_tag, reason}
    status is one of the four constants above. Read-only, makes no
    assumption about game_results at all -- this is a PREGAME check by
    design, unlike persist_features_v2's retrospective safeguard.
    """
    row = conn.execute(
        "SELECT sport FROM games_canonical WHERE canonical_game_id = ?", (canonical_game_id,)
    ).fetchone()
    if row is None:
        return {
            "status": UNKNOWN_INSUFFICIENT_EVIDENCE,
            "has_preseason_tag": False, "has_regular_tag": False,
            "reason": f"canonical_game_id {canonical_game_id} not found in games_canonical",
        }
    sport = row[0]

    tags = conn.execute(
        "SELECT DISTINCT sport FROM market_snapshots WHERE canonical_game_id = ?", (canonical_game_id,)
    ).fetchall()
    tag_set = {t[0] for t in tags}

    has_preseason_tag = "nfl_preseason" in tag_set
    has_regular_tag = sport in tag_set

    if not tag_set:
        return {
            "status": UNKNOWN_INSUFFICIENT_EVIDENCE,
            "has_preseason_tag": False, "has_regular_tag": False,
            "reason": "zero market_snapshots rows exist for this game -- no evidence either way, "
                      "absence of a preseason tag is NOT sufficient positive evidence of eligibility",
        }

    if has_preseason_tag and has_regular_tag:
        return {
            "status": AMBIGUOUS_CONFLICTING_TAGS,
            "has_preseason_tag": True, "has_regular_tag": True,
            "reason": f"canonical_game_id {canonical_game_id} has BOTH 'nfl_preseason' and {sport!r} "
                      f"tagged market_snapshots rows -- a real, structurally-possible conflict per the "
                      f"audit; failing closed rather than guessing which is correct",
        }

    if has_preseason_tag:
        return {
            "status": NOT_ELIGIBLE_PRESEASON,
            "has_preseason_tag": True, "has_regular_tag": False,
            "reason": "this game has 'nfl_preseason' tagged market_snapshots -- not eligible for "
                      "regular-season RUN SLATE evaluation",
        }

    if has_regular_tag:
        return {
            "status": ELIGIBLE,
            "has_preseason_tag": False, "has_regular_tag": True,
            "reason": f"real {sport!r} tagged market_snapshots exist with zero preseason tags -- "
                      f"positively confirmed regular-season eligible",
        }

    return {
        "status": UNKNOWN_INSUFFICIENT_EVIDENCE,
        "has_preseason_tag": False, "has_regular_tag": False,
        "reason": f"market_snapshots exist with unexpected sport tag(s) {tag_set} for a game whose "
                  f"identity sport is {sport!r} -- neither confirms nor denies eligibility, failing closed",
    }
