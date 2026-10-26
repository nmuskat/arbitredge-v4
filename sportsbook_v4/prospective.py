"""
sportsbook_v4/prospective.py -- immutable prospective decision records.

One record per (game, policy_version, decision_point), written strictly
before kickoff, never updated or deleted (table triggers + CHECK). A second
write with byte-identical content is a no-op; a second write with
DIFFERENT content raises -- it is never silently replaced. This is the
property the legacy experimental_mixed_signals path lacks (upsert/delete).
Grading against results is milestone 2.
"""

from dataclasses import asdict

from sportsbook_v4 import ENGINE_VERSION
from sportsbook_v4.params import params_hash
from sportsbook_v4.util import canonical_json, json_hash, sha256_hex, to_epoch


class ImmutableRecordConflict(RuntimeError):
    pass


class NotProspective(RuntimeError):
    pass


def record_decision(conn, *, game, sport, decision_point, decided_at, run_id, ledger_watermark, assessment):
    if to_epoch(decided_at) >= to_epoch(game.commence_time):
        raise NotProspective(f"{game.canonical_game_id}: decided_at {decided_at} is not before kickoff")
    if to_epoch(ledger_watermark) > to_epoch(decided_at):
        raise NotProspective("ledger watermark is later than the decision time")
    need = assessment["financial_need"]
    play = assessment["official_play"]
    payload = {k: asdict(v) for k, v in assessment.items()}
    policy = play.policy_version
    decision_id = sha256_hex("v4_decision", game.canonical_game_id, policy, decision_point)[:40]
    row = {
        "decision_id": decision_id, "canonical_game_id": game.canonical_game_id, "sport": sport,
        "decision_point": decision_point, "policy_version": policy, "decided_at": decided_at,
        "kickoff": game.commence_time, "run_id": run_id, "ledger_watermark": ledger_watermark,
        "financial_need_side": need.side, "official_play": play.decision, "play_side": play.side,
        "play_line": None, "play_price": None, "assessment_json": canonical_json(payload),
        "engine_version": ENGINE_VERSION, "params_hash": params_hash(),
    }
    row["record_hash"] = json_hash(row)
    existing = conn.execute(
        "SELECT record_hash FROM v4_prospective_decision WHERE decision_id=?", (decision_id,)
    ).fetchone()
    if existing is not None:
        if existing[0] == row["record_hash"]:
            return decision_id, "unchanged"
        raise ImmutableRecordConflict(f"decision {decision_id} already recorded with different content")
    cols = list(row)
    conn.execute(f"INSERT INTO v4_prospective_decision ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 [row[c] for c in cols])
    conn.commit()
    return decision_id, "written"
