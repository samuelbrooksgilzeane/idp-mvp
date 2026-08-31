"""Request-scoped timing metrics for safe operational visibility.

Only aggregate timings and statement counts are collected. SQL text, parameter values,
document content and identifiers are deliberately excluded from response headers.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass
class RequestMetrics:
    sql_statement_count: int = 0
    sql_duration_ms: float = 0.0


_request_metrics: ContextVar[RequestMetrics | None] = ContextVar(
    "request_metrics", default=None
)


def begin_request_metrics() -> Token[RequestMetrics | None]:
    """Start collecting metrics for the current request and return its reset token."""
    return _request_metrics.set(RequestMetrics())


def current_request_metrics() -> RequestMetrics | None:
    return _request_metrics.get()


def record_sql_statement(duration_ms: float) -> None:
    """Record a completed Databricks SQL statement when a request is being served."""
    metrics = _request_metrics.get()
    if metrics is None:
        return
    metrics.sql_statement_count += 1
    metrics.sql_duration_ms += duration_ms


def reset_request_metrics(token: Token[RequestMetrics | None]) -> None:
    _request_metrics.reset(token)
