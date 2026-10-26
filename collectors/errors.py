"""
collectors/errors.py

A small, structured exception hierarchy so failure handling in
base_collector.py (and collection_log.error_type) is based on real classes,
not string-matching.
"""


class CollectorError(Exception):
    """Base class for all collector-framework errors."""


class RetryableRequestError(CollectorError):
    """Raised for transient failures worth retrying: timeouts, connection
    errors, HTTP 429, and selected 5xx responses."""

    def __init__(self, message: str, retry_after_seconds: float = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class NonRetryableRequestError(CollectorError):
    """Raised for permanent failures: most 4xx responses, malformed
    requests, etc. Retrying would not help."""


class RetriesExhaustedError(CollectorError):
    """Raised when the maximum retry count is reached without success."""


class RequestBudgetExceededError(CollectorError):
    """Raised when a single collection run would exceed its configured
    per-run request ceiling -- a safeguard against a bug silently burning
    paid API credits."""


class ParsingError(CollectorError):
    """Raised when a response cannot be parsed into the expected shape.
    Distinguished from request-level errors so 'we got data back but
    couldn't understand it' is logged differently from 'we couldn't reach
    the source at all'."""


class RecordSkippedError(CollectorError):
    """Not really an error -- used internally to signal that one specific
    record within an otherwise-successful response should be skipped
    (e.g. unresolved team identity) without failing the whole run."""
