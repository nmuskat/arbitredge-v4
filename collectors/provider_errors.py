"""
collectors/provider_errors.py

Safe extraction of provider error diagnostics from a failed HTTP response.

Why this exists: base_collector.request_with_retry() used to raise on any
non-retryable 4xx with only "Non-retryable HTTP <status> from <url>" -- the
provider's structured error (The Odds API returns an `error_code` such as
INVALID_KEY, DEACTIVATED_KEY or OUT_OF_USAGE_CREDITS plus a human message)
and the credit headers (x-requests-remaining / x-requests-used /
x-requests-last) were discarded. Those are exactly the facts needed to tell
"bad key" from "out of credits".

SECURITY RULES (all enforced here, all tested):
  * Never read or record request headers, response.url, response.request,
    cookies, or the raw response body. Only:
      - an allowlist of response headers,
      - `error_code` if it matches ^[A-Z][A-Z0-9_]{1,63}$,
      - `message` after secret scrubbing, control-char stripping, truncation,
      - `details_url` only if https, on an allowlisted provider host, with no
        query string or fragment,
      - body length and SHA-256 (lets two failures be compared without
        storing the body).
  * Scrubbing removes every known secret value (and its URL-encoded form),
    key=value style credentials, bearer/authorization tokens, and any long
    hex/alphanumeric token -- so even a provider that unexpectedly echoes the
    key back cannot leak it into logs or the database.
"""

import hashlib
import json
import re
from urllib.parse import urlsplit

REDACTED = "***REDACTED***"

SAFE_RESPONSE_HEADERS = (
    "x-requests-remaining", "x-requests-used", "x-requests-last",
    "retry-after", "content-type", "date",
)
PROVIDER_DETAIL_HOSTS = {"the-odds-api.com", "www.the-odds-api.com", "api.the-odds-api.com"}
MAX_MESSAGE_CHARS = 300
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

# Classifications (stable strings: they are written into collection_log.error_detail
# and parsed back by operations/odds_circuit_breaker.py).
QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
AUTH_INVALID_KEY = "AUTH_INVALID_KEY"
AUTH_DEACTIVATED_KEY = "AUTH_DEACTIVATED_KEY"
AUTH_UNKNOWN = "AUTH_UNKNOWN"              # 401/403 with no recognised provider code
RATE_LIMITED = "RATE_LIMITED"
CLIENT_ERROR = "CLIENT_ERROR"
SERVER_ERROR = "SERVER_ERROR"
AUTH_CLASSES = frozenset({QUOTA_EXHAUSTED, AUTH_INVALID_KEY, AUTH_DEACTIVATED_KEY, AUTH_UNKNOWN})

# Provider codes documented by The Odds API (api-error-codes page). The HTTP
# status each one uses was NOT confirmed during this investigation, so the
# classification is driven by the code itself, not by the status.
_CODE_CLASS = {
    "OUT_OF_USAGE_CREDITS": QUOTA_EXHAUSTED,
    "INVALID_KEY": AUTH_INVALID_KEY,
    "MISSING_KEY": AUTH_INVALID_KEY,
    "DEACTIVATED_KEY": AUTH_DEACTIVATED_KEY,
}

_KV_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|token|secret|password|key)\b(\s*[=:]\s*|\"\s*:\s*\")([^&\s\"',;}]+)"
)
_AUTH_HEADER_RE = re.compile(r"(?i)\b(authorization|x-api-key)\b(\s*[:=]\s*)(\S+(?:\s+\S+)?)")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{24,}\b")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _secret_pattern(secret):
    """Matches the secret with every character either literal or %XX-encoded
    (any case) -- covers raw, quote()d, fully-encoded and re-cased echoes."""
    parts = []
    for ch in secret:
        enc = "".join(f"%{b:02X}" for b in ch.encode("utf-8"))
        parts.append(f"(?:{re.escape(ch)}|{re.escape(enc)})")
    return re.compile("".join(parts), re.IGNORECASE)


def scrub(text, secrets=()):
    """Remove secrets from arbitrary text. Order: exact secret values first
    (raw and URL-encoded), then credential patterns, then long opaque tokens."""
    if text is None:
        return None
    out = str(text)
    for s in sorted({s for s in secrets if s}, key=len, reverse=True):
        out = _secret_pattern(s).sub(REDACTED, out)
    out = _KV_SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", out)
    out = _AUTH_HEADER_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", out)
    out = _BEARER_RE.sub(f"Bearer {REDACTED}", out)
    out = _LONG_TOKEN_RE.sub(REDACTED, out)
    return out


def _clean_message(value, secrets):
    if not isinstance(value, str):
        return None
    msg = _CONTROL_RE.sub(" ", scrub(value, secrets)).strip()
    return msg[:MAX_MESSAGE_CHARS] + ("…" if len(msg) > MAX_MESSAGE_CHARS else "") if msg else None


def _clean_details_url(value):
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.hostname not in PROVIDER_DETAIL_HOSTS or parts.query or parts.fragment:
        return None
    if parts.username or parts.password or _LONG_TOKEN_RE.search(parts.path):
        return None
    return f"https://{parts.hostname}{parts.path}"


def safe_headers(headers):
    out = {}
    for name in SAFE_RESPONSE_HEADERS:
        try:
            v = headers.get(name)
        except Exception:
            v = None
        if v is not None:
            out[name] = scrub(str(v))[:64]
    return out


def classify(status, error_code, headers=None):
    if error_code in _CODE_CLASS:
        return _CODE_CLASS[error_code]
    if status == 429:
        return RATE_LIMITED
    if status in (401, 403):
        return AUTH_UNKNOWN
    if status is not None and status >= 500:
        return SERVER_ERROR
    return CLIENT_ERROR


def extract_provider_error(response, secrets=()):
    """
    Returns a sanitized diagnostics dict for a failed response. Never raises:
    a malformed/hostile response degrades to fewer fields, not an exception
    that would mask the original HTTP failure.
    """
    status = getattr(response, "status_code", None)
    headers = getattr(response, "headers", {}) or {}
    try:
        body = response.content if isinstance(getattr(response, "content", None), (bytes, bytearray)) else b""
    except Exception:
        body = b""
    diag = {
        "http_status": status,
        "error_code": None,
        "message": None,
        "details_url": None,
        "safe_headers": safe_headers(headers),
        "body_length": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest() if body else None,
        "body_format": "empty" if not body else "non_json",
    }
    if body:
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            diag["body_format"] = "json"
            code = data.get("error_code")
            if isinstance(code, str) and _ERROR_CODE_RE.match(code):
                diag["error_code"] = code
            diag["message"] = _clean_message(data.get("message"), secrets)
            diag["details_url"] = _clean_details_url(data.get("details_url"))
        elif data is not None:
            diag["body_format"] = "json_non_object"
    remaining = diag["safe_headers"].get("x-requests-remaining")
    diag["classification"] = classify(status, diag["error_code"], diag["safe_headers"])
    if remaining is not None:
        try:
            diag["credits_remaining"] = float(remaining)
        except ValueError:
            diag["credits_remaining"] = None
    return diag


def summarize(diag):
    """One-line, secret-free summary. This exact format is parsed by
    operations/odds_circuit_breaker.py -- keep the `key=value` tokens stable."""
    parts = [
        f"HTTP {diag.get('http_status')}",
        f"classification={diag.get('classification')}",
        f"error_code={diag.get('error_code') or 'none'}",
    ]
    hdr = diag.get("safe_headers") or {}
    for h in ("x-requests-remaining", "x-requests-used", "x-requests-last"):
        if h in hdr:
            parts.append(f"{h}={hdr[h]}")
    if diag.get("message"):
        parts.append(f"message={json.dumps(diag['message'])}")
    if diag.get("details_url"):
        parts.append(f"details_url={diag['details_url']}")
    parts.append(f"body={diag.get('body_format')}/{diag.get('body_length')}B"
                 + (f"/sha256:{diag['body_sha256'][:16]}" if diag.get("body_sha256") else ""))
    return " ".join(parts)
