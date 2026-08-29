"""Immutable storage for deterministic validation runs and their observations."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from idp_app.services.document_models import ValidationResultRecord, ValidationRunRecord
from idp_app.services.document_registry import DatabricksDocumentRegistry

RUN_COLUMNS = (
    "validation_run_id",
    "document_id",
    "extraction_run_id",
    "schema_id",
    "schema_version",
    "schema_hash",
    "validator_version",
    "status",
    "document_status",
    "requested_by",
    "started_at",
    "completed_at",
)

RESULT_COLUMNS = (
    "validation_run_id",
    "extraction_run_id",
    "document_id",
    "rule_id",
    "field_path",
    "validator_type",
    "severity",
    "status",
    "message",
    "actual_value",
    "expected_value",
    "suggested_value",
    "evidence",
    "validator_version",
    "prompt_hash",
    "created_at",
)


class ValidationRunRepository(Protocol):
    def save(
        self, run: ValidationRunRecord, results: list[ValidationResultRecord]
    ) -> None: ...

    def get(self, validation_run_id: str) -> ValidationRunRecord | None: ...

    def list_for_document(self, document_id: str) -> list[ValidationRunRecord]: ...

    def latest(self, document_id: str) -> ValidationRunRecord | None: ...

    def list_results(self, validation_run_id: str) -> list[ValidationResultRecord]: ...

    def find_business_duplicates(
        self, document_id: str, seller_name: str | None, invoice_number: str | None
    ) -> list[str]: ...


class SQLiteValidationRunRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS validation_runs (
                    validation_run_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    extraction_run_id TEXT NOT NULL,
                    schema_id TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    schema_hash TEXT NOT NULL,
                    validator_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    document_status TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS validation_runs_document_history
                ON validation_runs (document_id, started_at DESC, validation_run_id DESC);
                CREATE TABLE IF NOT EXISTS validation_results (
                    validation_run_id TEXT NOT NULL,
                    extraction_run_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    field_path TEXT,
                    validator_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    actual_value TEXT,
                    expected_value TEXT,
                    suggested_value TEXT,
                    evidence TEXT,
                    validator_version TEXT NOT NULL,
                    prompt_hash TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS validation_results_run
                ON validation_results (validation_run_id);
                """
            )

    def save(self, run: ValidationRunRecord, results: list[ValidationResultRecord]) -> None:
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO validation_runs ({', '.join(RUN_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in RUN_COLUMNS)})",
                _run_values(run),
            )
            connection.executemany(
                f"INSERT INTO validation_results ({', '.join(RESULT_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in RESULT_COLUMNS)})",
                [_result_values(result) for result in results],
            )

    def get(self, validation_run_id: str) -> ValidationRunRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM validation_runs WHERE validation_run_id = ?",
                (validation_run_id,),
            ).fetchone()
        return _run_from_row(row) if row else None

    def list_for_document(self, document_id: str) -> list[ValidationRunRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM validation_runs WHERE document_id = ? "
                "ORDER BY started_at DESC, validation_run_id DESC",
                (document_id,),
            ).fetchall()
        return [_run_from_row(row) for row in rows]

    def latest(self, document_id: str) -> ValidationRunRecord | None:
        runs = self.list_for_document(document_id)
        return runs[0] if runs else None

    def list_results(self, validation_run_id: str) -> list[ValidationResultRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM validation_results WHERE validation_run_id = ? "
                "ORDER BY rowid",
                (validation_run_id,),
            ).fetchall()
        return [_result_from_row(row) for row in rows]

    def find_business_duplicates(
        self, document_id: str, seller_name: str | None, invoice_number: str | None
    ) -> list[str]:
        if not seller_name or not invoice_number:
            return []
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    "SELECT DISTINCT document_id FROM invoice_candidates "
                    "WHERE seller_name = ? AND invoice_number = ? AND document_id <> ? "
                    "ORDER BY document_id",
                    (seller_name, invoice_number, document_id),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [cast(str, row["document_id"]) for row in rows]


class DatabricksValidationRunRepository:
    def __init__(
        self,
        sql_client: DatabricksDocumentRegistry,
        catalog: str,
        project_schema: str,
        table_prefix: str,
    ) -> None:
        self._sql = sql_client
        prefix = f"{catalog}.{project_schema}.{table_prefix}"
        self._runs = f"{prefix}_validation_runs"
        self._results = f"{prefix}_validation_results"
        self._candidates = f"{prefix}_invoice_candidates"

    def save(self, run: ValidationRunRecord, results: list[ValidationResultRecord]) -> None:
        self._sql.execute_sql(
            f"INSERT INTO {self._runs} ({', '.join(RUN_COLUMNS)}) VALUES ("
            ":validation_run_id, :document_id, :extraction_run_id, :schema_id, "
            "CAST(:schema_version AS INT), :schema_hash, :validator_version, :status, "
            ":document_status, :requested_by, CAST(:started_at AS TIMESTAMP), "
            "CAST(:completed_at AS TIMESTAMP))",
            {
                "validation_run_id": run.validation_run_id,
                "document_id": run.document_id,
                "extraction_run_id": run.extraction_run_id,
                "schema_id": run.schema_id,
                "schema_version": run.schema_version,
                "schema_hash": run.schema_hash,
                "validator_version": run.validator_version,
                "status": run.status,
                "document_status": run.document_status,
                "requested_by": run.requested_by,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
            },
        )
        if not results:
            return
        # One statement for every observation keeps the run and its evidence a single write.
        rows: list[str] = []
        parameters: dict[str, Any] = {}
        for index, result in enumerate(results):
            rows.append(
                f"(:r{index}_validation_run_id, :r{index}_extraction_run_id, "
                f":r{index}_document_id, :r{index}_rule_id, :r{index}_field_path, "
                f":r{index}_validator_type, :r{index}_severity, :r{index}_status, "
                f":r{index}_message, :r{index}_actual_value, :r{index}_expected_value, "
                f":r{index}_suggested_value, :r{index}_evidence, :r{index}_validator_version, "
                f":r{index}_prompt_hash, CAST(:r{index}_created_at AS TIMESTAMP))"
            )
            for column, value in zip(RESULT_COLUMNS, _result_values(result), strict=True):
                parameters[f"r{index}_{column}"] = value
        self._sql.execute_sql(
            f"INSERT INTO {self._results} ({', '.join(RESULT_COLUMNS)}) VALUES "
            + ", ".join(rows),
            parameters,
        )

    def get(self, validation_run_id: str) -> ValidationRunRecord | None:
        rows = self._sql.execute_sql(
            f"SELECT {', '.join(RUN_COLUMNS)} FROM {self._runs} "
            "WHERE validation_run_id = :validation_run_id LIMIT 1",
            {"validation_run_id": validation_run_id},
        )
        return _run_from_values(rows[0]) if rows else None

    def list_for_document(self, document_id: str) -> list[ValidationRunRecord]:
        rows = self._sql.execute_sql(
            f"SELECT {', '.join(RUN_COLUMNS)} FROM {self._runs} "
            "WHERE document_id = :document_id "
            "ORDER BY started_at DESC, validation_run_id DESC",
            {"document_id": document_id},
        )
        return [_run_from_values(row) for row in rows]

    def latest(self, document_id: str) -> ValidationRunRecord | None:
        runs = self.list_for_document(document_id)
        return runs[0] if runs else None

    def list_results(self, validation_run_id: str) -> list[ValidationResultRecord]:
        rows = self._sql.execute_sql(
            f"SELECT {', '.join(RESULT_COLUMNS)} FROM {self._results} "
            "WHERE validation_run_id = :validation_run_id ORDER BY rule_id, field_path",
            {"validation_run_id": validation_run_id},
        )
        return [_result_from_values(row) for row in rows]

    def find_business_duplicates(
        self, document_id: str, seller_name: str | None, invoice_number: str | None
    ) -> list[str]:
        if not seller_name or not invoice_number:
            return []
        rows = self._sql.execute_sql(
            f"SELECT DISTINCT document_id FROM {self._candidates} "
            "WHERE seller_name = :seller_name AND invoice_number = :invoice_number "
            "AND document_id <> :document_id ORDER BY document_id",
            {
                "seller_name": seller_name,
                "invoice_number": invoice_number,
                "document_id": document_id,
            },
        )
        return [str(row[0]) for row in rows]


def _run_values(run: ValidationRunRecord) -> tuple[Any, ...]:
    return (
        run.validation_run_id, run.document_id, run.extraction_run_id, run.schema_id,
        run.schema_version, run.schema_hash, run.validator_version, run.status,
        run.document_status, run.requested_by, run.started_at.isoformat(),
        run.completed_at.isoformat() if run.completed_at else None,
    )


def _result_values(result: ValidationResultRecord) -> tuple[Any, ...]:
    return (
        result.validation_run_id, result.extraction_run_id, result.document_id, result.rule_id,
        result.field_path, result.validator_type, result.severity, result.status, result.message,
        result.actual_value, result.expected_value, result.suggested_value, result.evidence,
        result.validator_version, result.prompt_hash, result.created_at.isoformat(),
    )


def _run_from_row(row: sqlite3.Row) -> ValidationRunRecord:
    return _run_from_values([row[column] for column in RUN_COLUMNS])


def _result_from_row(row: sqlite3.Row) -> ValidationResultRecord:
    return _result_from_values([row[column] for column in RESULT_COLUMNS])


def _run_from_values(values: Any) -> ValidationRunRecord:
    return ValidationRunRecord(
        validation_run_id=str(values[0]),
        document_id=str(values[1]),
        extraction_run_id=str(values[2]),
        schema_id=str(values[3]),
        schema_version=int(values[4]),
        schema_hash=str(values[5]),
        validator_version=str(values[6]),
        status=str(values[7]),
        document_status=str(values[8]),
        requested_by=str(values[9]),
        started_at=_timestamp(values[10]),
        completed_at=_timestamp(values[11]) if values[11] else None,
    )


def _result_from_values(values: Any) -> ValidationResultRecord:
    return ValidationResultRecord(
        validation_run_id=str(values[0]),
        extraction_run_id=str(values[1]),
        document_id=str(values[2]),
        rule_id=str(values[3]),
        field_path=str(values[4]) if values[4] is not None else None,
        validator_type=str(values[5]),
        severity=str(values[6]),
        status=str(values[7]),
        message=str(values[8]),
        actual_value=str(values[9]) if values[9] is not None else None,
        expected_value=str(values[10]) if values[10] is not None else None,
        suggested_value=str(values[11]) if values[11] is not None else None,
        evidence=str(values[12]) if values[12] is not None else None,
        validator_version=str(values[13]),
        prompt_hash=str(values[14]) if values[14] is not None else None,
        created_at=_timestamp(values[15]),
    )


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
