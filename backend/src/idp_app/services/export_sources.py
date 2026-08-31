"""Bulk, read-only source data for schema-driven exports.

An export needs the retained model response, its immutable schema and the document name. Loading
those through the detail service caused several sequential SQL statements and a failed cache write
for every selected run. These repositories deliberately fetch the complete export contract in one
joined statement and never touch the generic cache tables.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from idp_app.services.document_models import ExtractionRunRecord
from idp_app.services.document_registry import DatabricksDocumentRegistry
from idp_app.services.extraction_runs import RUN_COLUMNS, extraction_run_from_values
from idp_app.services.schema_models import SchemaRecord
from idp_app.services.schema_registry import SCHEMA_COLUMNS, schema_record_from_values


@dataclass(frozen=True)
class ExportSource:
    run: ExtractionRunRecord
    schema: SchemaRecord
    document_name: str


class ExportSourceRepository(Protocol):
    def get_many(self, run_ids: list[str]) -> list[ExportSource]: ...


class SQLiteExportSourceRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def get_many(self, run_ids: list[str]) -> list[ExportSource]:
        if not run_ids:
            return []
        placeholders = ", ".join("?" for _ in run_ids)
        run_columns = ", ".join(
            f"runs.{column} AS run_{column}" for column in RUN_COLUMNS
        )
        schema_columns = ", ".join(
            f"schemas.{column} AS schema_{column}" for column in SCHEMA_COLUMNS
        )
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                f"SELECT {run_columns}, {schema_columns}, "
                "documents.file_name AS document_name "
                "FROM extraction_runs AS runs "
                "JOIN documents ON documents.document_id = runs.document_id "
                "JOIN schema_registry AS schemas "
                "ON schemas.schema_id = runs.schema_id "
                "AND schemas.schema_version = runs.schema_version "
                f"WHERE runs.extraction_run_id IN ({placeholders})",
                run_ids,
            ).fetchall()
        finally:
            connection.close()
        return [
            ExportSource(
                run=extraction_run_from_values(
                    {column: row[f"run_{column}"] for column in RUN_COLUMNS}
                ),
                schema=schema_record_from_values(
                    {column: row[f"schema_{column}"] for column in SCHEMA_COLUMNS}
                ),
                document_name=str(row["document_name"]),
            )
            for row in rows
        ]


class DatabricksExportSourceRepository:
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
        self._documents = f"{prefix}_documents"
        self._schemas = f"{prefix}_schema_registry"

    def get_many(self, run_ids: list[str]) -> list[ExportSource]:
        if not run_ids:
            return []
        parameters: dict[str, object] = {
            f"run_id_{index}": run_id for index, run_id in enumerate(run_ids)
        }
        markers = ", ".join(f":run_id_{index}" for index in range(len(run_ids)))
        run_columns = (
            "runs.extraction_run_id, runs.document_id, runs.parse_run_id, runs.schema_id, "
            "runs.schema_version, runs.schema_hash, runs.extractor_version, "
            "TO_JSON(runs.options), TO_JSON(runs.ai_result), runs.error_message, runs.status, "
            "runs.requested_by, runs.job_run_id, runs.started_at, runs.completed_at"
        )
        schema_columns = ", ".join(f"schemas.{column}" for column in SCHEMA_COLUMNS)
        rows = self._sql.execute_sql(
            f"SELECT {run_columns}, {schema_columns}, documents.file_name "
            f"FROM {self._runs} AS runs "
            f"JOIN {self._documents} AS documents ON documents.document_id = runs.document_id "
            f"JOIN {self._schemas} AS schemas ON schemas.schema_id = runs.schema_id "
            "AND schemas.schema_version = runs.schema_version "
            f"WHERE runs.extraction_run_id IN ({markers})",
            parameters,
        )
        run_column_count = len(RUN_COLUMNS)
        schema_column_count = len(SCHEMA_COLUMNS)
        return [
            ExportSource(
                run=extraction_run_from_values(
                    dict(zip(RUN_COLUMNS, row[:run_column_count], strict=True))
                ),
                schema=schema_record_from_values(
                    dict(
                        zip(
                            SCHEMA_COLUMNS,
                            row[run_column_count : run_column_count + schema_column_count],
                            strict=True,
                        )
                    )
                ),
                document_name=row[-1],
            )
            for row in rows
        ]
