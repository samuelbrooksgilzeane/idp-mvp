"""Bounded retries for warehouse statements that are safe to repeat.

Only use this for statements whose second run cannot change the outcome of the first: revision-
checked UPDATEs (a repeat after a commit matches no row) and MERGEs that insert only when missing.
Callers must still resolve "0 rows affected" after a retry by reading back, because the first
attempt may have committed before its error reached us.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from databricks.sdk.errors import (
    Aborted,
    DeadlineExceeded,
    InternalError,
    RequestLimitExceeded,
    TemporarilyUnavailable,
    TooManyRequests,
)
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout

logger = logging.getLogger(__name__)
T = TypeVar("T")

DELTA_CONFLICTS = (
    "DELTA_CONCURRENT",
    "ConcurrentAppendException",
    "ConcurrentWriteException",
    "ConcurrentDeleteReadException",
)
TRANSIENT_TYPES: tuple[type[BaseException], ...] = (
    Aborted,
    DeadlineExceeded,
    InternalError,
    RequestLimitExceeded,
    TemporarilyUnavailable,
    TooManyRequests,
    ConnectionError,
    TimeoutError,
    RequestsConnectionError,
    RequestsTimeout,
)
# Failed statements surface as RuntimeError carrying the warehouse's message.
TRANSIENT_MESSAGES = (
    "TEMPORARILY_UNAVAILABLE",
    "SERVICE_UNAVAILABLE",
    "temporarily unavailable",
    "Connection reset",
    "timed out",
)
ATTEMPTS = 3


def retry_reason(error: BaseException) -> str | None:
    """A short code when ``error`` is worth one more try, else None."""
    text = str(error)
    conflict = next((code for code in DELTA_CONFLICTS if code in text), None)
    if conflict:
        return conflict
    if isinstance(error, TRANSIENT_TYPES):
        return type(error).__name__
    return next((marker for marker in TRANSIENT_MESSAGES if marker in text), None)


class RetryOutcome:
    """Whether any failed attempt might nevertheless have committed."""

    uncertain = False


def run_with_retries(
    operation: Callable[[], T], outcome: RetryOutcome | None = None, *, label: str
) -> T:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return operation()
        except Exception as error:
            reason = retry_reason(error)
            if attempt == ATTEMPTS or reason is None:
                raise
            # A Delta conflict aborts the transaction; any other failure may have committed.
            if outcome is not None and reason not in DELTA_CONFLICTS:
                outcome.uncertain = True
            logger.warning(
                "Retrying %s after a transient warehouse failure",
                label,
                extra={"error_code": reason, "attempt": attempt},
            )
            time.sleep(0.25 * 2 ** (attempt - 1))
    raise AssertionError("unreachable")
