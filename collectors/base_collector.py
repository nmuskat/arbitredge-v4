"""
collectors/base_collector.py

Shared infrastructure for every ArbitrEdge Needs collector. No individual
collector should reimplement HTTP retry logic, rate limiting, provenance
capture, or collection logging -- it should inherit/use this.

Design summary (see the accompanying report for full rationale):

  - Every collection attempt produces exactly one collection_log row,
    success or failure, via the CollectionRun context manager. There is no
    code path that can exit without logging.
  - Retries are bounded, exponential-backoff, and only for genuinely
    transient failures (timeout, connection error, HTTP 429, and a
    specific allow-list of 5xx codes). Most 4xx responses fail immediately,
    no retry.
  - A per-run request-count ceiling and a minimum inter-request interval
    guard against a bug silently burning paid API credits.
  - Raw responses are stored ONCE per HTTP request in `raw_responses` and
    referenced by foreign key from parsed rows -- never duplicated per
    parsed record.
  - Each parsed record is inserted in its own transaction (commit/rollback
    per record). A bad record is skipped and logged; it does not roll back
    or block records already successfully written in the same run. This
    is a deliberate choice, not an oversight -- see the report for the
    atomicity tradeoff discussion.
  - Secrets are read from environment variables only, and are redacted
    from every URL/error string before it reaches a log line, an
    exception, or the database.
"""

import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import requests

from collectors.config import (
    DEFAULT_BACKOFF_BASE_SECONDS,
    DEFAULT_BACKOFF_FACTOR,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAX_BACKOFF_SECONDS,
    DEFAULT_MAX_REQUESTS_PER_RUN,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
    DEFAULT_READ_TIMEOUT_SECONDS,
    RETRYABLE_STATUS_CODES,
    redact,
)
from collectors.errors import (
    NonRetryableRequestError,
    RequestBudgetExceededError,
    RetriesExhaustedError,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _get_logger(collector_name: str) -> logging.Logger:
    logger = logging.getLogger(f"arbitredge.collectors.{collector_name}")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    return logger


class CollectionRun:
    """
    Tracks one collection attempt end to end and guarantees exactly one
    collection_log row is written, whether the run succeeds, partially
    succeeds, or fails outright.

    Usage:
        with collector.new_run() as run:
            ... do work, calling run.records_captured += 1 / run.records_skipped += 1 ...
    On a raised exception inside the `with` block, the run is logged as a
    failure (error_type/error_detail captured, secrets redacted) and the
    exception is SUPPRESSED -- a total collector failure must not crash a
    scheduler that is also running other collectors. Callers that need to
    know whether a run succeeded should check run.success after the block,
    not rely on exception propagation.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        collector_name: str,
        source: str,
        scheduled_at: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.conn = conn
        self.collector_name = collector_name
        self.source = source
        self.scheduled_at = scheduled_at or _now()
        self.executed_at: Optional[str] = None
        self.records_captured = 0
        self.records_skipped = 0
        self.retry_count = 0
        self.success: Optional[bool] = None
        self.error_type: Optional[str] = None
        self.error_detail: Optional[str] = None
        self._logger = logger or _get_logger(collector_name)

    def __enter__(self) -> "CollectionRun":
        self.executed_at = _now()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is None:
            self.success = True
        else:
            self.success = False
            self.error_type = exc_type.__name__
            self.error_detail = redact(str(exc_val))[:2000]
            self._logger.error(
                "Collection run failed: %s: %s", self.error_type, self.error_detail
            )
        self._write_log()
        return True  # suppress exceptions -- always logged above first

    def _write_log(self) -> None:
        self.conn.execute(
            "INSERT INTO collection_log "
            "(collector_name, scheduled_at, executed_at, source, success, "
            "records_captured, records_skipped, error_type, error_detail, retry_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.collector_name, self.scheduled_at, self.executed_at, self.source,
                1 if self.success else 0, self.records_captured, self.records_skipped,
                self.error_type, self.error_detail, self.retry_count,
            ),
        )
        self.conn.commit()


class BaseCollector:
    PARSER_VERSION = "unset"  # subclasses MUST override, e.g. "v1"

    def __init__(
        self,
        conn: sqlite3.Connection,
        collector_name: str,
        source: str,
        session: Optional[requests.Session] = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout: float = DEFAULT_READ_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE_SECONDS,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        max_backoff: float = DEFAULT_MAX_BACKOFF_SECONDS,
        min_request_interval: float = DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
        max_requests_per_run: int = DEFAULT_MAX_REQUESTS_PER_RUN,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.conn = conn
        self.collector_name = collector_name
        self.source = source
        self.session = session or requests.Session()
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_factor = backoff_factor
        self.max_backoff = max_backoff
        self.min_request_interval = min_request_interval
        self.max_requests_per_run = max_requests_per_run
        self.sleep_fn = sleep_fn

        self.logger = _get_logger(collector_name)
        self._request_count = 0
        self._last_request_time: Optional[float] = None

    # ---------------------------------------------------------------
    # Run lifecycle
    # ---------------------------------------------------------------

    def new_run(self, scheduled_at: Optional[str] = None) -> CollectionRun:
        """Starts a new collection run and resets the per-run request budget."""
        self._request_count = 0
        return CollectionRun(self.conn, self.collector_name, self.source, scheduled_at, self.logger)

    # ---------------------------------------------------------------
    # HTTP with retry/backoff/budget/throttle
    # ---------------------------------------------------------------

    def request_with_retry(
        self, run: CollectionRun, method: str, url: str, **kwargs
    ) -> requests.Response:
        total_attempts = self.max_retries + 1

        for attempt_num in range(1, total_attempts + 1):
            self._enforce_request_budget()
            self._throttle()

            try:
                response = self.session.request(
                    method, url, timeout=(self.connect_timeout, self.read_timeout), **kwargs
                )
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                run.retry_count = attempt_num - 1
                if attempt_num == total_attempts:
                    raise RetriesExhaustedError(
                        f"Exhausted retries after {total_attempts} attempt(s) "
                        f"for {redact(url)}: {redact(str(e))}"
                    ) from None
                self.logger.warning(
                    "Attempt %d/%d failed (%s), retrying...",
                    attempt_num, total_attempts, type(e).__name__,
                )
                self._sleep_backoff(attempt_num)
                continue

            if response.status_code in RETRYABLE_STATUS_CODES:
                run.retry_count = attempt_num - 1
                if attempt_num == total_attempts:
                    raise RetriesExhaustedError(
                        f"Exhausted retries after {total_attempts} attempt(s) for {redact(url)}, "
                        f"last status {response.status_code}"
                    )
                retry_after = self._parse_retry_after(response)
                self.logger.warning(
                    "Attempt %d/%d got HTTP %d, retrying (retry_after=%s)...",
                    attempt_num, total_attempts, response.status_code, retry_after,
                )
                self._sleep_backoff(attempt_num, override_seconds=retry_after)
                continue

            if response.status_code >= 400:
                raise NonRetryableRequestError(
                    f"Non-retryable HTTP {response.status_code} from {redact(url)}"
                )

            usage_info = self.extract_usage_info(response)
            if usage_info:
                self.logger.info("API usage info: %s", usage_info)

            run.retry_count = attempt_num - 1
            return response

        # Unreachable in practice (loop always returns or raises), kept as a
        # defensive fallback so the function has no implicit-None return path.
        raise RetriesExhaustedError(f"Exhausted retries for {redact(url)}")

    def extract_usage_info(self, response: requests.Response) -> Optional[dict]:
        """
        Hook for subclasses to extract provider-specific usage/credit
        headers (e.g. the-odds-api.com's x-requests-remaining /
        x-requests-used / x-requests-last) without hard-coding any
        provider's header names into the base class. Default: no-op.
        """
        return None

    def _enforce_request_budget(self) -> None:
        self._request_count += 1
        if self._request_count > self.max_requests_per_run:
            raise RequestBudgetExceededError(
                f"Collector '{self.collector_name}' exceeded its per-run request "
                f"budget of {self.max_requests_per_run}. Aborting to protect API credits."
            )

    def _throttle(self) -> None:
        if self._last_request_time is not None:
            elapsed = time.monotonic() - self._last_request_time
            remaining = self.min_request_interval - elapsed
            if remaining > 0:
                self.sleep_fn(remaining)
        self._last_request_time = time.monotonic()

    def _sleep_backoff(self, attempt_num: int, override_seconds: Optional[float] = None) -> None:
        if override_seconds is not None:
            self.sleep_fn(min(override_seconds, self.max_backoff))
            return
        delay = min(self.backoff_base * (self.backoff_factor ** (attempt_num - 1)), self.max_backoff)
        self.sleep_fn(delay)

    @staticmethod
    def _parse_retry_after(response: requests.Response) -> Optional[float]:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            # HTTP-date form of Retry-After is not parsed here; fall back to
            # standard backoff in that case rather than guessing.
            return None

    # ---------------------------------------------------------------
    # Raw response storage (once per request, referenced by parsed rows)
    # ---------------------------------------------------------------

    def store_raw_response(
        self,
        url: str,
        http_status: Optional[int],
        response_body: Optional[str],
        request_params_redacted: Optional[str] = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO raw_responses "
            "(collector_name, source, request_url, request_params_redacted, "
            "retrieved_at, http_status, response_body, parser_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.collector_name, self.source, redact(url), request_params_redacted,
                _now(), http_status, response_body, self.PARSER_VERSION,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    # ---------------------------------------------------------------
    # Per-record safe insert (own transaction; a bad record doesn't roll
    # back records already committed earlier in the same run)
    # ---------------------------------------------------------------

    def safe_insert(
        self, run: CollectionRun, execute_fn: Callable[[sqlite3.Connection], None], context_note: str = ""
    ) -> bool:
        try:
            execute_fn(self.conn)
            self.conn.commit()
            run.records_captured += 1
            return True
        except sqlite3.Error as e:
            self.conn.rollback()
            self.logger.error("DB insert failed (%s): %s", context_note, redact(str(e)))
            run.records_skipped += 1
            return False

    # ---------------------------------------------------------------
    # Identity-resolution hooks
    # ---------------------------------------------------------------

    def resolve_game_or_skip(
        self, run: CollectionRun, game_resolver, source, source_game_key, sport,
        home_raw, away_raw, commence_time_raw,
    ) -> Optional[int]:
        canonical_game_id = game_resolver.resolve_or_create(
            source, source_game_key, sport, home_raw, away_raw, commence_time_raw
        )
        if canonical_game_id is None:
            run.records_skipped += 1
            self.logger.warning(
                "Skipping unresolved game (%s/%s): %r vs %r",
                source, source_game_key, home_raw, away_raw,
            )
        return canonical_game_id
