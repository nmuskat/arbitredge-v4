"""
sims_v5/schema.py -- validation of SIMS web-export files (format arbsims-web-export, schema 1).

validate(doc, now, allow_fixture) returns a list of problems; an empty list means the file may be shown. A file is
rejected, never repaired, when: its format, schema version, kind or league is wrong; its payload hash does not match;
it was exported in the future; it is a TEST FIXTURE outside a test/preview build; it contains a college game or a
non-NFL game id; a probability is outside 0-1; an American price is impossible; a Top 5 rank or row id is
inconsistent; or required fields are missing.
"""

import hashlib
import json
import re
from datetime import datetime, timezone

FORMAT = "arbsims-web-export"
SCHEMA_VERSION = 1
KINDS = ("projections", "edges", "history")
MAX_COMPRESSED = 5 * 1024 * 1024
MAX_JSON = 50 * 1024 * 1024
NFL_GAME_ID = re.compile(r"^\d{4}_\d{2}_[A-Z]{2,3}_[A-Z]{2,3}$")
FUTURE_SLACK_S = 600


def canonical(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def payload_hash(body):
    return hashlib.sha256(canonical(body).encode()).hexdigest()


def _t(x):
    try:
        d = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _prob(v):
    return v is None or (isinstance(v, (int, float)) and 0.0 <= v <= 1.0)


def _price(v):
    return isinstance(v, int) and not isinstance(v, bool) and abs(v) >= 100


def _projections(b, out):
    games = b.get("games")
    if not isinstance(games, list):
        return out.append("projections: games missing")
    for g in games:
        gid = str(g.get("game_id", ""))
        if not NFL_GAME_ID.match(gid):
            out.append(f"projections: not an NFL game id: {gid!r}")
            continue
        if g.get("forecast_status") != "official":
            continue
        for k in ("forecast_id", "model_version", "forecast_created_at", "n_sims", "game", "players"):
            if g.get(k) in (None, ""):
                out.append(f"projections {gid}: {k} missing")
        if not isinstance(g.get("n_sims"), int) or g.get("n_sims", 0) <= 0:
            out.append(f"projections {gid}: n_sims invalid")
        wp = (g.get("game") or {}).get("win_prob") or {}
        if not wp or not all(_prob(v) for v in wp.values()):
            out.append(f"projections {gid}: win probability invalid")
        for p in g.get("players") or []:
            if not (_prob(p.get("play_prob")) and _prob(p.get("anytime_td"))):
                out.append(f"projections {gid}: probability outside 0-1 for {p.get('name')}")
                break


def _edges(b, out):
    m, rows = b.get("meta"), b.get("rows")
    if not isinstance(m, dict) or not isinstance(rows, list):
        return out.append("edges: meta or rows missing")
    for k in ("slate_date", "kind", "version", "report_id", "rows_sha256"):
        if m.get(k) in (None, ""):
            out.append(f"edges: meta.{k} missing")
    ids = b.get("row_ids") or []
    top = b.get("top_row_ids") or []
    if len(ids) != len(rows) or not set(top) <= set(ids) or len(top) > 5:
        out.append("edges: Top 5 row ids inconsistent")
    for r in rows:
        gid = str(r.get("slate_game") or "")
        if gid and not NFL_GAME_ID.match(gid):
            out.append(f"edges: not an NFL game id: {gid!r}")
            break
        if not all(_prob(r.get(k)) for k in ("p_win", "p_cal", "break_even", "market_no_vig", "p_loss", "p_push")):
            out.append(f"edges: probability outside 0-1 ({r.get('player_name')}, {r.get('market_label')})")
            break
        if r.get("price") is not None and not _price(r.get("price")):
            out.append(f"edges: impossible American price {r.get('price')!r}")
            break
        if r.get("rank") is not None and r.get("rank") not in (1, 2, 3, 4, 5):
            out.append(f"edges: rank {r.get('rank')!r}")
            break


def _history(b, out):
    if not isinstance(b.get("top5"), dict):
        out.append("history: top5 missing")


def validate(doc, now=None, allow_fixture=False):
    now = now or datetime.now(timezone.utc)
    out = []
    if not isinstance(doc, dict):
        return ["not a JSON object"]
    if doc.get("format") != FORMAT:
        out.append(f"format {doc.get('format')!r}")
    if doc.get("schema_version") != SCHEMA_VERSION:
        out.append(f"schema version {doc.get('schema_version')!r} (expected {SCHEMA_VERSION})")
    if doc.get("kind") not in KINDS:
        out.append(f"kind {doc.get('kind')!r}")
    if doc.get("league") != "NFL":
        out.append(f"league {doc.get('league')!r}: only NFL is accepted")
    if doc.get("fixture") and not allow_fixture:
        out.append("TEST FIXTURE file: refused outside a test or preview build")
    t = _t(doc.get("exported_at"))
    if t is None:
        out.append("exported_at missing or invalid")
    elif (t - now).total_seconds() > FUTURE_SLACK_S:
        out.append("exported in the future")
    body = doc.get("body")
    if not isinstance(body, dict):
        return out + ["body missing"]
    if doc.get("payload_sha256") != payload_hash(body):
        out.append("payload hash does not match")
    if not doc.get("sims_model_version"):
        out.append("sims_model_version missing")
    if out:
        return out
    {"projections": _projections, "edges": _edges, "history": _history}[doc["kind"]](body, out)
    return out
