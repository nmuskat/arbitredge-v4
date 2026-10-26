"""
book_need/prospective_record.py

Append-only write of a computed inference into
book_need_prospective_records. A later as_of always creates a NEW row --
the append-only triggers structurally enforce that this table is never
edited after insert, matching every other append-only table in this
project.
"""

import json
from datetime import datetime, timezone


def write_prospective_record(conn, inference_result):
    if inference_result is None:
        return None

    written_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn.execute(
        "INSERT INTO book_need_prospective_records "
        "(canonical_game_id, sport, model_version, as_of, written_at, current_segment_name, "
        "inferred_book_need_side, need_strength, data_quality, fallback_occurred, contradiction_flag, "
        "full_record_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            inference_result["canonical_game_id"], inference_result["sport"],
            inference_result["model_version"], inference_result["as_of"], written_at,
            inference_result["current_segment_name"], inference_result["inferred_book_need_side"],
            inference_result["need_strength"], inference_result["data_quality"],
            int(inference_result["fallback_occurred"]), int(inference_result["contradiction_flag"]),
            json.dumps(inference_result, default=str),
        ),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
