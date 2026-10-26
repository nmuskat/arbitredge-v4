"""Deterministic hashing, seeding and UTC time helpers (no wall-clock reads)."""

import hashlib
import json
from datetime import datetime, timezone

ISO_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def sha256_hex(*parts):
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()


def seed_int(*parts):
    """64-bit integer seed from documented parts (never Python's hash())."""
    return int(sha256_hex(*parts)[:16], 16)


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def json_hash(obj):
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()


def parse_ts(ts):
    """ISO-8601 string (with Z or offset) -> aware UTC datetime."""
    if isinstance(ts, datetime):
        dt = ts
    else:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"timestamp must be timezone-aware: {ts!r}")
    return dt.astimezone(timezone.utc)


def to_epoch(ts):
    return int(parse_ts(ts).timestamp())


def from_epoch(seconds):
    return datetime.fromtimestamp(int(seconds), tz=timezone.utc).strftime(ISO_FMT)
