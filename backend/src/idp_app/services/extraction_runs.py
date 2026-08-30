from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, cast

from idp_app.services.document_models import (
    ExtractedFieldRecord,
    ExtractedRecordRow,
    ExtractionRunRecord,
    GenericFieldRow,
    InvoiceCandidateRecord,
    InvoiceLineCandidateRecord,
)
from idp_app.services.document_registry import DatabricksDocumentRegistry
from idp_app.services.extraction_result import leaf_field_name

RUN_COLUMNS = (
    "extraction_run_id",
    "document_id",
    "parse_run_id",
    "schema_id",
    "schema_version",
    "schema_hash",
    "extractor_version",
    "options",
    "ai_result",
    "error_message",
    "status",
    "requested_by",
    "job_run_id",
    "started_at",
    "completed_at",
)

LINE_COLUMNS = (
    "extraction_run_id",
    "document_id",
    "invoice_index",
    "line_number",
    "description",
    "quantity",
    "unit_price",
    "tax",
    "amount",
)


class ExtractionRunRepository(Protocol):
    def create(self, run: ExtractionRunRecord) -> None: ...

    def assign_job_run(self, extraction_run_id: str, job_run_id: int) -> None: ...

    def retain_raw(self, extraction_run_id: str, ai_result: dict[str, Any]) -> None: ...

    def complete(
        self,
        extraction_run_id: str,
        fields: list[ExtractedFieldRecord],
        candidates: list[InvoiceCandidateRecord],
        lines: list[InvoiceLineCandidateRecord],
    ) -> None: ...

    def fail(self, extraction_run_id: str, error_message: str) -> None: ...

    def get(self, extraction_run_id: str) -> ExtractionRunRecord | None: ...

    def list_for_document(self, document_id: str) -> list[ExtractionRunRecord]: ...

    def list_all(self) -> list[ExtractionRunRecord]: ...

    def list_for_job_run(self, job_run_id: int) -> list[ExtractionRunRecord]: ...

    def latest_successful(self, document_id: str) -> ExtractionRunRecord | None: ...

    def list_fields(self, extraction_run_id: str) -> list[ExtractedFieldRecord]: ...

    def list_candidates(self, extraction_run_id: str) -> list[InvoiceCandidateRecord]: ...

    def list_lines(self, extraction_run_id: str) -> list[InvoiceLineCandidateRecord]: ...

    def persist_generic(
        self, records: list[ExtractedRecordRow], fields: list[GenericFieldRow]
    ) -> None:
        """Write-through cache: persist a run's recursive record tree the first time it is
        computed, so later reads (get_records, list_summaries) hit these tables instead of
        re-walking the retained ai_extract JSON. Idempotent -- retrying the same run recomputes
        identical record_ids, so calling this twice with the same input is a no-op the second
        time."""
        ...

    def list_generic_records(self, extraction_run_id: str) -> list[ExtractedRecordRow]: ...

    def list_generic_fields(self, extraction_run_id: str) -> list[GenericFieldRow]: ...


class SQLiteExtractionRunRepository:
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
                CREATE TABLE IF NOT EXISTS extraction_runs (
                    extraction_run_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    parse_run_id TEXT NOT NULL,
                    schema_id TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    schema_hash TEXT NOT NULL,
                    extractor_version TEXT NOT NULL,
                    options TEXT NOT NULL,
                    ai_result TEXT,
                    error_message TEXT,
                    status TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    job_run_id INTEGER,
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS extraction_runs_document_history
                ON extraction_runs (document_id, started_at DESC, extraction_run_id DESC);
                CREATE TABLE IF NOT EXISTS extracted_fields (
                    extraction_run_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    field_path TEXT NOT NULL,
                    field_type TEXT NOT NULL,
                    value TEXT,
                    value_string TEXT,
                    confidence_score REAL,
                    citation_ids TEXT NOT NULL,
                    citations TEXT NOT NULL,
                    extraction_error TEXT,
                    PRIMARY KEY (extraction_run_id, field_path)
                );
                CREATE TABLE IF NOT EXISTS extracted_records (
                    run_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    parent_record_id TEXT,
                    schema_path TEXT NOT NULL,
                    instance_path TEXT NOT NULL,
                    ordinal INTEGER,
                    PRIMARY KEY (record_id)
                );
                CREATE TABLE IF NOT EXISTS invoice_line_candidates (
                    extraction_run_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    invoice_index INTEGER NOT NULL DEFAULT 0,
                    line_number INTEGER NOT NULL,
                    description TEXT,
                    quantity TEXT,
                    unit_price TEXT,
                    tax TEXT,
                    amount TEXT,
                    PRIMARY KEY (extraction_run_id, invoice_index, line_number)
                );
                CREATE TABLE IF NOT EXISTS invoice_candidates (
                    case_id TEXT,
                    document_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    template_id TEXT NOT NULL,
                    invoice_number TEXT,
                    invoice_date TEXT,
                    seller_name TEXT,
                    subtotal TEXT,
                    discount_amount TEXT,
                    tax_amount TEXT,
                    total_amount TEXT,
                    currency TEXT,
                    extraction_run_id TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    invoice_index INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (extraction_run_id, invoice_index)
                );
                """
            )
            # Additive migration for a local registry.sqlite3 created before the generic
            # record tree existed. New columns are nullable, so already-written invoice
            # projection rows are unaffected.
            existing_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(extracted_fields)").fetchall()
            }
            for column in (
                "record_id", "schema_path", "instance_path", "declared_type",
                "validation_status", "validation_message",
            ):
                if column not in existing_columns:
                    connection.execute(f"ALTER TABLE extracted_fields ADD COLUMN {column} TEXT")

    def create(self, run: ExtractionRunRecord) -> None:
        placeholders = ", ".join("?" for _ in RUN_COLUMNS)
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO extraction_runs ({', '.join(RUN_COLUMNS)}) VALUES ({placeholders})",
                _run_values(run),
            )

    def assign_job_run(self, extraction_run_id: str, job_run_id: int) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE extraction_runs SET job_run_id = ? "
                "WHERE extraction_run_id = ? AND status = 'RUNNING'",
                (job_run_id, extraction_run_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Extraction run is not available for job assignment")

    def retain_raw(self, extraction_run_id: str, ai_result: dict[str, Any]) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE extraction_runs SET ai_result = ? "
                "WHERE extraction_run_id = ? AND status = 'RUNNING' AND ai_result IS NULL",
                (_json(ai_result), extraction_run_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Extraction run raw result cannot be retained")

    def complete(
        self,
        extraction_run_id: str,
        fields: list[ExtractedFieldRecord],
        candidates: list[InvoiceCandidateRecord],
        lines: list[InvoiceLineCandidateRecord],
    ) -> None:
        with self._connect() as connection:
            for field in fields:
                connection.execute(
                    "INSERT INTO extracted_fields "
                    "(extraction_run_id, document_id, field_path, field_type, value, "
                    "value_string, confidence_score, citation_ids, citations, extraction_error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    _field_values(field),
                )
            connection.executemany(
                "INSERT INTO invoice_candidates "
                "(case_id, document_id, source_path, template_id, invoice_number, invoice_date, "
                "seller_name, subtotal, discount_amount, tax_amount, total_amount, currency, "
                "extraction_run_id, schema_version, invoice_index) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [_candidate_values(candidate) for candidate in candidates],
            )
            connection.executemany(
                f"INSERT INTO invoice_line_candidates ({', '.join(LINE_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in LINE_COLUMNS)})",
                [_line_values(line) for line in lines],
            )
            cursor = connection.execute(
                "UPDATE extraction_runs SET status = 'EXTRACTED', error_message = NULL, "
                "completed_at = ? WHERE extraction_run_id = ? AND status = 'RUNNING' "
                "AND ai_result IS NOT NULL",
                (datetime.now(UTC).isoformat(), extraction_run_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Extraction run is not eligible for completion")

    def fail(self, extraction_run_id: str, error_message: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE extraction_runs SET error_message = ?, status = 'FAILED', completed_at = ? "
                "WHERE extraction_run_id = ? AND status = 'RUNNING'",
                (error_message[:500], datetime.now(UTC).isoformat(), extraction_run_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Extraction run is not eligible for failure completion")

    def get(self, extraction_run_id: str) -> ExtractionRunRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM extraction_runs WHERE extraction_run_id = ? LIMIT 1",
                (extraction_run_id,),
            ).fetchone()
        return _sqlite_row_to_run(row) if row else None

    def list_for_document(self, document_id: str) -> list[ExtractionRunRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM extraction_runs WHERE document_id = ? "
                "ORDER BY started_at DESC, extraction_run_id DESC",
                (document_id,),
            ).fetchall()
        return [_sqlite_row_to_run(row) for row in rows]

    def list_all(self) -> list[ExtractionRunRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM extraction_runs ORDER BY started_at DESC, extraction_run_id DESC"
            ).fetchall()
        return [_sqlite_row_to_run(row) for row in rows]

    def list_for_job_run(self, job_run_id: int) -> list[ExtractionRunRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM extraction_runs WHERE job_run_id = ? "
                "ORDER BY started_at, extraction_run_id",
                (job_run_id,),
            ).fetchall()
        return [_sqlite_row_to_run(row) for row in rows]

    def latest_successful(self, document_id: str) -> ExtractionRunRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM extraction_runs WHERE document_id = ? AND status = 'EXTRACTED' "
                "ORDER BY completed_at DESC, extraction_run_id DESC LIMIT 1",
                (document_id,),
            ).fetchone()
        return _sqlite_row_to_run(row) if row else None

    def list_fields(self, extraction_run_id: str) -> list[ExtractedFieldRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM extracted_fields WHERE extraction_run_id = ? ORDER BY field_path",
                (extraction_run_id,),
            ).fetchall()
        return [_sqlite_row_to_field(row) for row in rows]

    def list_candidates(self, extraction_run_id: str) -> list[InvoiceCandidateRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM invoice_candidates WHERE extraction_run_id = ? "
                "ORDER BY invoice_index",
                (extraction_run_id,),
            ).fetchall()
        return [_sqlite_row_to_candidate(row) for row in rows]

    def list_lines(self, extraction_run_id: str) -> list[InvoiceLineCandidateRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM invoice_line_candidates WHERE extraction_run_id = ? "
                "ORDER BY invoice_index, line_number",
                (extraction_run_id,),
            ).fetchall()
        return [_line_from_values([row[column] for column in LINE_COLUMNS]) for row in rows]

    def persist_generic(
        self, records: list[ExtractedRecordRow], fields: list[GenericFieldRow]
    ) -> None:
        with self._connect() as connection:
            for record in records:
                connection.execute(
                    "INSERT OR IGNORE INTO extracted_records "
                    "(run_id, document_id, record_id, parent_record_id, schema_path, "
                    "instance_path, ordinal) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    _record_values(record),
                )
            for field in fields:
                cursor = connection.execute(
                    "UPDATE extracted_fields SET record_id = ?, schema_path = ?, "
                    "instance_path = ?, declared_type = ?, validation_status = ?, "
                    "validation_message = ? WHERE extraction_run_id = ? AND field_path = ?",
                    _generic_field_update_values(field),
                )
                if cursor.rowcount == 0:
                    # Defensive fallback: flatten_result and walk_extraction should always
                    # agree on the same leaf set, but if a row was never written at
                    # extraction time, insert one rather than silently dropping this field.
                    connection.execute(
                        "INSERT OR IGNORE INTO extracted_fields "
                        "(extraction_run_id, document_id, field_path, field_type, value, "
                        "value_string, confidence_score, citation_ids, citations, "
                        "extraction_error, record_id, schema_path, instance_path, "
                        "declared_type, validation_status, validation_message) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        _generic_field_insert_values(field),
                    )

    def list_generic_records(self, extraction_run_id: str) -> list[ExtractedRecordRow]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM extracted_records WHERE run_id = ?", (extraction_run_id,)
            ).fetchall()
        return [_sqlite_row_to_record(row) for row in rows]

    def list_generic_fields(self, extraction_run_id: str) -> list[GenericFieldRow]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM extracted_fields WHERE extraction_run_id = ? "
                "AND record_id IS NOT NULL ORDER BY instance_path",
                (extraction_run_id,),
            ).fetchall()
        return [_sqlite_row_to_generic_field(row) for row in rows]


class DatabricksExtractionRunRepository:
    def __init__(
        self,
        sql_client: DatabricksDocumentRegistry,
        catalog: str,
        project_schema: str,
        table_prefix: str,
    ) -> None:
        self._sql = sql_client
        prefix = f"{catalog}.{project_schema}.{table_prefix}"
        self._runs = f"{prefix}_extraction_runs"
        self._fields = f"{prefix}_extracted_fields"
        self._records = f"{prefix}_extracted_records"
        self._candidates = f"{prefix}_invoice_candidates"
        self._lines = f"{prefix}_invoice_line_candidates"

    def create(self, run: ExtractionRunRecord) -> None:
        self._sql.execute_sql(
            f"INSERT INTO {self._runs} (extraction_run_id, document_id, parse_run_id, schema_id, "
            "schema_version, schema_hash, extractor_version, options, ai_result, error_message, "
            "status, requested_by, job_run_id, started_at, completed_at) VALUES "
            "(:extraction_run_id, :document_id, :parse_run_id, :schema_id, "
            "CAST(:schema_version AS INT), :schema_hash, :extractor_version, "
            "map('version', '2.1', 'mode', 'precision', 'enableCitations', 'true', "
            "'enableConfidenceScores', 'true', 'idempotency_key', :idempotency_key), "
            "NULL, NULL, 'RUNNING', :requested_by, NULL, CAST(:started_at AS TIMESTAMP), NULL)",
            {
                "extraction_run_id": run.extraction_run_id,
                "document_id": run.document_id,
                "parse_run_id": run.parse_run_id,
                "schema_id": run.schema_id,
                "schema_version": run.schema_version,
                "schema_hash": run.schema_hash,
                "extractor_version": run.extractor_version,
                "idempotency_key": run.options["idempotency_key"],
                "requested_by": run.requested_by,
                "started_at": run.started_at,
            },
        )

    def assign_job_run(self, extraction_run_id: str, job_run_id: int) -> None:
        self._sql.execute_sql(
            f"UPDATE {self._runs} SET job_run_id = CAST(:job_run_id AS BIGINT) "
            "WHERE extraction_run_id = :extraction_run_id AND status = 'RUNNING'",
            {"extraction_run_id": extraction_run_id, "job_run_id": job_run_id},
        )

    def retain_raw(self, extraction_run_id: str, ai_result: dict[str, Any]) -> None:
        self._sql.execute_sql(
            f"UPDATE {self._runs} SET ai_result = PARSE_JSON(:ai_result) "
            "WHERE extraction_run_id = :extraction_run_id AND status = 'RUNNING' "
            "AND ai_result IS NULL",
            {"extraction_run_id": extraction_run_id, "ai_result": _json(ai_result)},
        )

    def complete(
        self,
        extraction_run_id: str,
        fields: list[ExtractedFieldRecord],
        candidates: list[InvoiceCandidateRecord],
        lines: list[InvoiceLineCandidateRecord],
    ) -> None:
        for field in fields:
            self._sql.execute_sql(
                f"INSERT INTO {self._fields} VALUES (:extraction_run_id, :document_id, "
                ":field_path, :field_type, PARSE_JSON(:value), :value_string, "
                "CAST(:confidence_score AS DOUBLE), from_json(:citation_ids, 'array<int>'), "
                "PARSE_JSON(:citations), :extraction_error)",
                {
                    "extraction_run_id": field.extraction_run_id,
                    "document_id": field.document_id,
                    "field_path": field.field_path,
                    "field_type": field.field_type,
                    "value": _json(field.value),
                    "value_string": field.value_string,
                    "confidence_score": field.confidence_score,
                    "citation_ids": _json(field.citation_ids),
                    "citations": _json(field.citations),
                    "extraction_error": field.extraction_error,
                },
            )
        for candidate in candidates:
            self._sql.execute_sql(
                f"INSERT INTO {self._candidates} VALUES (:case_id, :document_id, :source_path, "
                ":template_id, :invoice_number, CAST(:invoice_date AS DATE), :seller_name, "
                "CAST(:subtotal AS DECIMAL(18,2)), CAST(:discount_amount AS DECIMAL(18,2)), "
                "CAST(:tax_amount AS DECIMAL(18,2)), CAST(:total_amount AS DECIMAL(18,2)), "
                ":currency, :extraction_run_id, CAST(:schema_version AS INT), "
                "CAST(:invoice_index AS INT))",
                _candidate_parameters(candidate),
            )
        for line in lines:
            self._sql.execute_sql(
                f"INSERT INTO {self._lines} VALUES (:extraction_run_id, :document_id, "
                "CAST(:line_number AS INT), :description, "
                "CAST(:quantity AS DECIMAL(18,4)), CAST(:unit_price AS DECIMAL(18,2)), "
                "CAST(:tax AS DECIMAL(18,2)), CAST(:amount AS DECIMAL(18,2)), "
                "CAST(:invoice_index AS INT))",
                {
                    "extraction_run_id": line.extraction_run_id,
                    "document_id": line.document_id,
                    "invoice_index": line.invoice_index,
                    "line_number": line.line_number,
                    "description": line.description,
                    "quantity": _text(line.quantity),
                    "unit_price": _text(line.unit_price),
                    "tax": _text(line.tax),
                    "amount": _text(line.amount),
                },
            )
        self._sql.execute_sql(
            f"UPDATE {self._runs} SET status = 'EXTRACTED', error_message = NULL, "
            "completed_at = CURRENT_TIMESTAMP() WHERE extraction_run_id = :extraction_run_id "
            "AND status = 'RUNNING' AND ai_result IS NOT NULL",
            {"extraction_run_id": extraction_run_id},
        )

    def fail(self, extraction_run_id: str, error_message: str) -> None:
        self._sql.execute_sql(
            f"UPDATE {self._runs} SET error_message = :error_message, status = 'FAILED', "
            "completed_at = CURRENT_TIMESTAMP() WHERE extraction_run_id = :extraction_run_id "
            "AND status = 'RUNNING'",
            {"extraction_run_id": extraction_run_id, "error_message": error_message[:500]},
        )

    def get(self, extraction_run_id: str) -> ExtractionRunRecord | None:
        rows = self._sql.execute_sql(
            self._select_runs() + " WHERE extraction_run_id = :extraction_run_id LIMIT 1",
            {"extraction_run_id": extraction_run_id},
        )
        return _databricks_row_to_run(rows[0]) if rows else None

    def list_for_document(self, document_id: str) -> list[ExtractionRunRecord]:
        rows = self._sql.execute_sql(
            self._select_runs() + " WHERE document_id = :document_id "
            "ORDER BY started_at DESC, extraction_run_id DESC LIMIT 100",
            {"document_id": document_id},
        )
        return [_databricks_row_to_run(row) for row in rows]

    def list_all(self) -> list[ExtractionRunRecord]:
        rows = self._sql.execute_sql(
            self._select_runs() + " ORDER BY started_at DESC, extraction_run_id DESC LIMIT 2000"
        )
        return [_databricks_row_to_run(row) for row in rows]

    def list_for_job_run(self, job_run_id: int) -> list[ExtractionRunRecord]:
        rows = self._sql.execute_sql(
            self._select_runs()
            + " WHERE job_run_id = :job_run_id ORDER BY started_at, extraction_run_id LIMIT 500",
            {"job_run_id": job_run_id},
        )
        return [_databricks_row_to_run(row) for row in rows]

    def latest_successful(self, document_id: str) -> ExtractionRunRecord | None:
        rows = self._sql.execute_sql(
            self._select_runs() + " WHERE document_id = :document_id AND status = 'EXTRACTED' "
            "ORDER BY completed_at DESC, extraction_run_id DESC LIMIT 1",
            {"document_id": document_id},
        )
        return _databricks_row_to_run(rows[0]) if rows else None

    def list_fields(self, extraction_run_id: str) -> list[ExtractedFieldRecord]:
        rows = self._sql.execute_sql(
            "SELECT extraction_run_id, document_id, field_path, field_type, TO_JSON(value), "
            "value_string, confidence_score, TO_JSON(citation_ids), TO_JSON(citations), "
            f"extraction_error FROM {self._fields} WHERE extraction_run_id = :extraction_run_id "
            "ORDER BY field_path",
            {"extraction_run_id": extraction_run_id},
        )
        return [_databricks_row_to_field(row) for row in rows]

    def list_candidates(self, extraction_run_id: str) -> list[InvoiceCandidateRecord]:
        rows = self._sql.execute_sql(
            "SELECT case_id, document_id, source_path, template_id, invoice_number, "
            "CAST(invoice_date AS STRING), seller_name, CAST(subtotal AS STRING), "
            "CAST(discount_amount AS STRING), CAST(tax_amount AS STRING), "
            "CAST(total_amount AS STRING), currency, extraction_run_id, schema_version, "
            f"CAST(invoice_index AS STRING) FROM {self._candidates} "
            "WHERE extraction_run_id = :extraction_run_id ORDER BY invoice_index",
            {"extraction_run_id": extraction_run_id},
        )
        return [_values_to_candidate(row) for row in rows]

    def list_lines(self, extraction_run_id: str) -> list[InvoiceLineCandidateRecord]:
        rows = self._sql.execute_sql(
            "SELECT extraction_run_id, document_id, CAST(invoice_index AS STRING), "
            "CAST(line_number AS STRING), description, "
            "CAST(quantity AS STRING), CAST(unit_price AS STRING), CAST(tax AS STRING), "
            f"CAST(amount AS STRING) FROM {self._lines} "
            "WHERE extraction_run_id = :extraction_run_id ORDER BY invoice_index, line_number",
            {"extraction_run_id": extraction_run_id},
        )
        return [_line_from_values(row) for row in rows]

    def persist_generic(
        self, records: list[ExtractedRecordRow], fields: list[GenericFieldRow]
    ) -> None:
        for record in records:
            self._sql.execute_sql(
                f"MERGE INTO {self._records} AS target USING (SELECT "
                ":run_id AS run_id, :document_id AS document_id, :record_id AS record_id, "
                ":parent_record_id AS parent_record_id, :schema_path AS schema_path, "
                ":instance_path AS instance_path, CAST(:ordinal AS INT) AS ordinal"
                ") AS source ON target.record_id = source.record_id "
                "WHEN NOT MATCHED THEN INSERT "
                "(run_id, document_id, record_id, parent_record_id, schema_path, "
                "instance_path, ordinal) VALUES (source.run_id, source.document_id, "
                "source.record_id, source.parent_record_id, source.schema_path, "
                "source.instance_path, source.ordinal)",
                _record_parameters(record),
            )
        for field in fields:
            self._sql.execute_sql(
                f"MERGE INTO {self._fields} AS target USING (SELECT "
                ":extraction_run_id AS extraction_run_id, :field_path AS field_path, "
                ":field_type AS field_type, PARSE_JSON(:value) AS value, "
                ":value_string AS value_string, "
                "CAST(:confidence_score AS DOUBLE) AS confidence_score, "
                "from_json(:citation_ids, 'array<int>') AS citation_ids, "
                "PARSE_JSON(:citations) AS citations, :document_id AS document_id, "
                ":record_id AS record_id, :schema_path AS schema_path, "
                ":instance_path AS instance_path, :declared_type AS declared_type, "
                ":validation_status AS validation_status, "
                ":validation_message AS validation_message"
                ") AS source "
                "ON target.extraction_run_id = source.extraction_run_id "
                "AND target.field_path = source.field_path "
                "WHEN MATCHED THEN UPDATE SET "
                "target.record_id = source.record_id, target.schema_path = source.schema_path, "
                "target.instance_path = source.instance_path, "
                "target.declared_type = source.declared_type, "
                "target.validation_status = source.validation_status, "
                "target.validation_message = source.validation_message "
                "WHEN NOT MATCHED THEN INSERT "
                "(extraction_run_id, document_id, field_path, field_type, value, value_string, "
                "confidence_score, citation_ids, citations, extraction_error, record_id, "
                "schema_path, instance_path, declared_type, validation_status, "
                "validation_message) "
                "VALUES (source.extraction_run_id, source.document_id, source.field_path, "
                "source.field_type, source.value, source.value_string, "
                "source.confidence_score, source.citation_ids, source.citations, NULL, "
                "source.record_id, source.schema_path, source.instance_path, "
                "source.declared_type, source.validation_status, source.validation_message)",
                _generic_field_parameters(field),
            )

    def list_generic_records(self, extraction_run_id: str) -> list[ExtractedRecordRow]:
        rows = self._sql.execute_sql(
            "SELECT run_id, document_id, record_id, parent_record_id, schema_path, "
            f"instance_path, CAST(ordinal AS STRING) FROM {self._records} "
            "WHERE run_id = :extraction_run_id",
            {"extraction_run_id": extraction_run_id},
        )
        return [_databricks_row_to_record(row) for row in rows]

    def list_generic_fields(self, extraction_run_id: str) -> list[GenericFieldRow]:
        rows = self._sql.execute_sql(
            "SELECT extraction_run_id, document_id, record_id, schema_path, instance_path, "
            "declared_type, TO_JSON(value), value_string, confidence_score, "
            "TO_JSON(citation_ids), TO_JSON(citations), validation_status, validation_message "
            f"FROM {self._fields} WHERE extraction_run_id = :extraction_run_id "
            "AND record_id IS NOT NULL ORDER BY instance_path",
            {"extraction_run_id": extraction_run_id},
        )
        return [_databricks_row_to_generic_field(row) for row in rows]

    def _select_runs(self) -> str:
        return (
            "SELECT extraction_run_id, document_id, parse_run_id, schema_id, schema_version, "
            "schema_hash, extractor_version, TO_JSON(options), TO_JSON(ai_result), error_message, "
            f"status, requested_by, job_run_id, started_at, completed_at FROM {self._runs}"
        )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _run_values(run: ExtractionRunRecord) -> tuple[object, ...]:
    return (
        run.extraction_run_id,
        run.document_id,
        run.parse_run_id,
        run.schema_id,
        run.schema_version,
        run.schema_hash,
        run.extractor_version,
        _json(run.options),
        _json(run.ai_result) if run.ai_result is not None else None,
        run.error_message,
        run.status,
        run.requested_by,
        run.job_run_id,
        run.started_at.isoformat(),
        run.completed_at.isoformat() if run.completed_at else None,
    )


def _field_values(field: ExtractedFieldRecord) -> tuple[object, ...]:
    return (
        field.extraction_run_id,
        field.document_id,
        field.field_path,
        field.field_type,
        _json(field.value),
        field.value_string,
        field.confidence_score,
        _json(field.citation_ids),
        _json(field.citations),
        field.extraction_error,
    )


def _candidate_values(candidate: InvoiceCandidateRecord) -> tuple[object, ...]:
    return (
        candidate.case_id,
        candidate.document_id,
        candidate.source_path,
        candidate.template_id,
        candidate.invoice_number,
        candidate.invoice_date.isoformat() if candidate.invoice_date else None,
        candidate.seller_name,
        str(candidate.subtotal) if candidate.subtotal is not None else None,
        str(candidate.discount_amount) if candidate.discount_amount is not None else None,
        str(candidate.tax_amount) if candidate.tax_amount is not None else None,
        str(candidate.total_amount) if candidate.total_amount is not None else None,
        candidate.currency,
        candidate.extraction_run_id,
        candidate.schema_version,
        candidate.invoice_index,
    )


def _candidate_parameters(candidate: InvoiceCandidateRecord) -> dict[str, object]:
    names = (
        "case_id", "document_id", "source_path", "template_id", "invoice_number",
        "invoice_date", "seller_name", "subtotal", "discount_amount", "tax_amount",
        "total_amount", "currency", "extraction_run_id", "schema_version", "invoice_index",
    )
    return dict(zip(names, _candidate_values(candidate), strict=True))


def _sqlite_row_to_run(row: sqlite3.Row) -> ExtractionRunRecord:
    return _values_to_run({column: row[column] for column in RUN_COLUMNS})


def _databricks_row_to_run(row: list[str]) -> ExtractionRunRecord:
    return _values_to_run(dict(zip(RUN_COLUMNS, row, strict=True)))


def _values_to_run(values: dict[str, object]) -> ExtractionRunRecord:
    return ExtractionRunRecord(
        extraction_run_id=cast(str, values["extraction_run_id"]),
        document_id=cast(str, values["document_id"]),
        parse_run_id=cast(str, values["parse_run_id"]),
        schema_id=cast(str, values["schema_id"]),
        schema_version=int(cast(str | int, values["schema_version"])),
        schema_hash=cast(str, values["schema_hash"]),
        extractor_version=cast(str, values["extractor_version"]),
        options=json.loads(cast(str, values["options"])),
        ai_result=json.loads(cast(str, values["ai_result"])) if values["ai_result"] else None,
        error_message=cast(str | None, values["error_message"]),
        status=cast(str, values["status"]),
        requested_by=cast(str, values["requested_by"]),
        job_run_id=int(cast(str | int, values["job_run_id"])) if values["job_run_id"] else None,
        started_at=_timestamp(cast(str, values["started_at"])),
        completed_at=(
            _timestamp(cast(str, values["completed_at"]))
            if values["completed_at"]
            else None
        ),
    )


def _sqlite_row_to_field(row: sqlite3.Row) -> ExtractedFieldRecord:
    values = [row[name] for name in (
        "extraction_run_id", "document_id", "field_path", "field_type", "value",
        "value_string", "confidence_score", "citation_ids", "citations", "extraction_error",
    )]
    return _databricks_row_to_field(values)


def _databricks_row_to_field(row: list[str]) -> ExtractedFieldRecord:
    return ExtractedFieldRecord(
        extraction_run_id=row[0], document_id=row[1], field_path=row[2], field_type=row[3],
        value=json.loads(row[4]) if row[4] else None, value_string=row[5] or None,
        confidence_score=float(row[6]) if row[6] else None,
        citation_ids=json.loads(row[7]) if row[7] else [],
        citations=json.loads(row[8]) if row[8] else [], extraction_error=row[9] or None,
    )


def _sqlite_row_to_candidate(row: sqlite3.Row) -> InvoiceCandidateRecord:
    names = (
        "case_id", "document_id", "source_path", "template_id", "invoice_number",
        "invoice_date", "seller_name", "subtotal", "discount_amount", "tax_amount",
        "total_amount", "currency", "extraction_run_id", "schema_version", "invoice_index",
    )
    return _values_to_candidate([row[name] for name in names])


def _values_to_candidate(row: list[str]) -> InvoiceCandidateRecord:
    return InvoiceCandidateRecord(
        case_id=row[0] or None, document_id=row[1], source_path=row[2], template_id=row[3],
        invoice_number=row[4] or None, invoice_date=date.fromisoformat(row[5]) if row[5] else None,
        seller_name=row[6] or None, subtotal=Decimal(row[7]) if row[7] else None,
        discount_amount=Decimal(row[8]) if row[8] else None,
        tax_amount=Decimal(row[9]) if row[9] else None,
        total_amount=Decimal(row[10]) if row[10] else None, currency=row[11] or None,
        extraction_run_id=row[12], schema_version=int(row[13]),
        invoice_index=int(row[14]),
    )


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _line_values(line: InvoiceLineCandidateRecord) -> tuple[Any, ...]:
    return (
        line.extraction_run_id, line.document_id, line.invoice_index, line.line_number,
        line.description,
        _text(line.quantity), _text(line.unit_price), _text(line.tax), _text(line.amount),
    )


def _line_from_values(values: Any) -> InvoiceLineCandidateRecord:
    def amount(raw: Any) -> Decimal | None:
        return None if raw is None else Decimal(str(raw))

    return InvoiceLineCandidateRecord(
        extraction_run_id=str(values[0]),
        document_id=str(values[1]),
        invoice_index=int(values[2]),
        line_number=int(values[3]),
        description=str(values[4]) if values[4] is not None else None,
        quantity=amount(values[5]),
        unit_price=amount(values[6]),
        tax=amount(values[7]),
        amount=amount(values[8]),
    )


def _record_values(record: ExtractedRecordRow) -> tuple[object, ...]:
    return (
        record.run_id,
        record.document_id,
        record.record_id,
        record.parent_record_id,
        record.schema_path,
        record.instance_path,
        record.ordinal,
    )


def _record_parameters(record: ExtractedRecordRow) -> dict[str, object]:
    names = (
        "run_id", "document_id", "record_id", "parent_record_id", "schema_path",
        "instance_path", "ordinal",
    )
    return dict(zip(names, _record_values(record), strict=True))


def _generic_field_update_values(field: GenericFieldRow) -> tuple[object, ...]:
    return (
        field.record_id,
        field.schema_path,
        field.instance_path,
        field.declared_type,
        field.validation_status,
        field.validation_message,
        field.run_id,
        field.instance_path,
    )


def _generic_field_insert_values(field: GenericFieldRow) -> tuple[object, ...]:
    return (
        field.run_id,
        field.document_id,
        field.instance_path,
        field.declared_type,
        _json(field.value),
        field.value_string,
        field.confidence_score,
        _json(field.citation_ids),
        _json(field.citations),
        None,
        field.record_id,
        field.schema_path,
        field.instance_path,
        field.declared_type,
        field.validation_status,
        field.validation_message,
    )


def _generic_field_parameters(field: GenericFieldRow) -> dict[str, object]:
    return {
        "extraction_run_id": field.run_id,
        "document_id": field.document_id,
        "field_path": field.instance_path,
        "field_type": field.declared_type,
        "value": _json(field.value),
        "value_string": field.value_string,
        "confidence_score": field.confidence_score,
        "citation_ids": _json(field.citation_ids),
        "citations": _json(field.citations),
        "record_id": field.record_id,
        "schema_path": field.schema_path,
        "instance_path": field.instance_path,
        "declared_type": field.declared_type,
        "validation_status": field.validation_status,
        "validation_message": field.validation_message,
    }


def _sqlite_row_to_record(row: sqlite3.Row) -> ExtractedRecordRow:
    return ExtractedRecordRow(
        run_id=row["run_id"],
        document_id=row["document_id"],
        record_id=row["record_id"],
        parent_record_id=row["parent_record_id"],
        schema_path=row["schema_path"],
        instance_path=row["instance_path"],
        ordinal=row["ordinal"],
    )


def _databricks_row_to_record(row: list[str]) -> ExtractedRecordRow:
    return ExtractedRecordRow(
        run_id=row[0],
        document_id=row[1],
        record_id=row[2],
        parent_record_id=row[3] or None,
        schema_path=row[4],
        instance_path=row[5],
        ordinal=int(row[6]) if row[6] else None,
    )


def _sqlite_row_to_generic_field(row: sqlite3.Row) -> GenericFieldRow:
    schema_path = row["schema_path"]
    return GenericFieldRow(
        run_id=row["extraction_run_id"],
        document_id=row["document_id"],
        record_id=row["record_id"],
        schema_path=schema_path,
        instance_path=row["instance_path"],
        field_name=leaf_field_name(schema_path),
        declared_type=row["declared_type"],
        value=json.loads(row["value"]) if row["value"] else None,
        value_string=row["value_string"],
        confidence_score=row["confidence_score"],
        citation_ids=json.loads(row["citation_ids"]) if row["citation_ids"] else [],
        citations=json.loads(row["citations"]) if row["citations"] else [],
        validation_status=row["validation_status"],
        validation_message=row["validation_message"],
    )


def _databricks_row_to_generic_field(row: list[str]) -> GenericFieldRow:
    schema_path = row[3]
    return GenericFieldRow(
        run_id=row[0],
        document_id=row[1],
        record_id=row[2],
        schema_path=schema_path,
        instance_path=row[4],
        field_name=leaf_field_name(schema_path),
        declared_type=row[5],
        value=json.loads(row[6]) if row[6] else None,
        value_string=row[7] or None,
        confidence_score=float(row[8]) if row[8] else None,
        citation_ids=json.loads(row[9]) if row[9] else [],
        citations=json.loads(row[10]) if row[10] else [],
        validation_status=row[11] or None,
        validation_message=row[12] or None,
    )
