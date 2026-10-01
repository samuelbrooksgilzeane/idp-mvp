from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.service import sql

from idp_app.services import document_registry
from idp_app.services.document_registry import DatabricksDocumentRegistry
from idp_app.services.sql_retry import SqlOutcomeUnknownError, retry_reason, run_with_retries


def response(
    state: sql.StatementState,
    rows: list[list[str]] | None = None,
    next_chunk: int | None = None,
    truncated: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        statement_id="s-1",
        status=SimpleNamespace(state=state, error=None),
        manifest=SimpleNamespace(schema=None, truncated=truncated),
        result=SimpleNamespace(data_array=rows, next_chunk_index=next_chunk),
    )


class FakeStatements:
    def __init__(self, first: SimpleNamespace, chunks: dict[int, SimpleNamespace] | None = None):
        self.first = first
        self.chunks = chunks or {}
        self.polls = 0
        self.cancelled: list[str] = []

    def execute_statement(self, **_: Any) -> SimpleNamespace:
        return self.first

    def get_statement(self, statement_id: str) -> SimpleNamespace:
        self.polls += 1
        return response(sql.StatementState.RUNNING)

    def get_statement_result_chunk_n(self, statement_id: str, index: int) -> SimpleNamespace:
        return self.chunks[index]

    def cancel_execution(self, statement_id: str) -> None:
        self.cancelled.append(statement_id)


def registry(statements: FakeStatements, deadline: float | None = None):
    client = SimpleNamespace(statement_execution=statements)
    return DatabricksDocumentRegistry(
        client,  # type: ignore[arg-type]
        "wh",
        "cat",
        "sch",
        "idp",
        statement_deadline_seconds=deadline,
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def perf_counter(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_rows_from_every_result_chunk_are_returned() -> None:
    statements = FakeStatements(
        response(sql.StatementState.SUCCEEDED, [["a"]], next_chunk=1),
        {
            1: SimpleNamespace(data_array=[["b"], ["c"]], next_chunk_index=2),
            2: SimpleNamespace(data_array=[["d"]], next_chunk_index=None),
        },
    )
    assert registry(statements).execute_sql("SELECT 1") == [["a"], ["b"], ["c"], ["d"]]


def test_truncated_result_is_rejected() -> None:
    statements = FakeStatements(response(sql.StatementState.SUCCEEDED, [["a"]], truncated=True))
    with pytest.raises(RuntimeError, match="truncated"):
        registry(statements).execute_sql("SELECT 1")


def test_stalled_statement_is_cancelled_at_the_deadline(monkeypatch) -> None:
    clock = FakeClock()
    monkeypatch.setattr(document_registry, "time", clock)
    statements = FakeStatements(response(sql.StatementState.RUNNING))
    with pytest.raises(SqlOutcomeUnknownError, match="may still have committed"):
        registry(statements, deadline=10).execute_sql("UPDATE t SET x = 1")
    assert statements.cancelled == ["s-1"]
    # Backs off to the cap and never sleeps past the deadline.
    assert clock.sleeps[:4] == [0.25, 0.5, 1.0, 2.0]
    assert max(clock.sleeps) == document_registry.MAX_POLL_SECONDS
    assert clock.now == pytest.approx(10)


def test_unknown_outcome_is_not_retried() -> None:
    attempts = []

    def stalled() -> None:
        attempts.append(1)
        raise SqlOutcomeUnknownError("statement passed its deadline; timed out")

    assert retry_reason(SqlOutcomeUnknownError("timed out")) is None
    with pytest.raises(SqlOutcomeUnknownError):
        run_with_retries(stalled, label="test")
    assert len(attempts) == 1
