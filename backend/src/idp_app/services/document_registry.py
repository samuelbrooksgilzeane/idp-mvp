from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import sql

from idp_app.services.document_models import DocumentRecord

DOCUMENT_COLUMNS = (
    "document_id",
    "case_id",
    "template_id",
    "use_case",
    "source_path",
    "file_name",
    "file_size",
    "content_sha256",
    "selected_schema_id",
    "selected_schema_version",
    "status",
    "uploaded_by",
    "uploaded_at",
    "updated_at",
)


class DuplicateDocumentError(Exception):
    def __init__(self, document: DocumentRecord) -> None:
        super().__init__(document.document_id)
        self.document = document


class InvalidDocumentStateError(Exception):
    def __init__(self, document: DocumentRecord, expected_statuses: set[str]) -> None:
        super().__init__(document.status)
        self.document = document
        self.expected_statuses = expected_statuses


class DocumentRegistry(Protocol):
    def find_by_hash(self, content_sha256: str) -> DocumentRecord | None: ...

    def add(self, document: DocumentRecord) -> None: ...

    def list_documents(self) -> list[DocumentRecord]: ...

    def get(self, document_id: str) -> DocumentRecord | None: ...

    def update_status(
        self,
        document_id: str,
        expected_statuses: set[str],
        new_status: str,
    ) -> DocumentRecord: ...


class SQLiteDocumentRegistry:
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    case_id TEXT,
                    template_id TEXT NOT NULL,
                    use_case TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL UNIQUE,
                    selected_schema_id TEXT,
                    selected_schema_version INTEGER,
                    status TEXT NOT NULL,
                    uploaded_by TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def find_by_hash(self, content_sha256: str) -> DocumentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE content_sha256 = ? LIMIT 1",
                (content_sha256,),
            ).fetchone()
        return _sqlite_row_to_document(row) if row else None

    def add(self, document: DocumentRecord) -> None:
        values = _document_values(document)
        placeholders = ", ".join("?" for _ in DOCUMENT_COLUMNS)
        try:
            with self._connect() as connection:
                connection.execute(
                    f"INSERT INTO documents ({', '.join(DOCUMENT_COLUMNS)}) "
                    f"VALUES ({placeholders})",
                    values,
                )
        except sqlite3.IntegrityError as error:
            duplicate = self.find_by_hash(document.content_sha256)
            if duplicate is not None:
                raise DuplicateDocumentError(duplicate) from error
            raise

    def list_documents(self) -> list[DocumentRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM documents ORDER BY uploaded_at DESC, document_id DESC"
            ).fetchall()
        return [_sqlite_row_to_document(row) for row in rows]

    def get(self, document_id: str) -> DocumentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE document_id = ? LIMIT 1",
                (document_id,),
            ).fetchone()
        return _sqlite_row_to_document(row) if row else None

    def update_status(
        self,
        document_id: str,
        expected_statuses: set[str],
        new_status: str,
    ) -> DocumentRecord:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE document_id = ? LIMIT 1",
                (document_id,),
            ).fetchone()
            if row is None:
                raise KeyError(document_id)
            document = _sqlite_row_to_document(row)
            if document.status not in expected_statuses:
                raise InvalidDocumentStateError(document, expected_statuses)
            connection.execute(
                "UPDATE documents SET status = ?, updated_at = ? WHERE document_id = ?",
                (new_status, now, document_id),
            )
        updated = self.get(document_id)
        if updated is None:
            raise RuntimeError("Document status update did not produce a readable row")
        return updated


class DatabricksDocumentRegistry:
    def __init__(
        self,
        client: WorkspaceClient,
        warehouse_id: str,
        catalog: str,
        project_schema: str,
        table_prefix: str,
    ) -> None:
        self._client = client
        self._warehouse_id = warehouse_id
        self._catalog = catalog
        self._project_schema = project_schema
        self._table = f"{catalog}.{project_schema}.{table_prefix}_documents"

    def find_by_hash(self, content_sha256: str) -> DocumentRecord | None:
        rows = self.execute_sql(
            f"SELECT {', '.join(DOCUMENT_COLUMNS)} FROM {self._table} "
            "WHERE content_sha256 = :content_sha256 "
            "ORDER BY uploaded_at DESC, document_id DESC LIMIT 1",
            {"content_sha256": content_sha256},
        )
        return _databricks_row_to_document(rows[0]) if rows else None

    def add(self, document: DocumentRecord) -> None:
        parameter_names = [name for name in DOCUMENT_COLUMNS if name not in {
            "selected_schema_id",
            "selected_schema_version",
        }]
        source_fields = ", ".join(f":{name} AS {name}" for name in parameter_names)
        insert_columns = ", ".join(DOCUMENT_COLUMNS)
        insert_values = ", ".join(
            (
                "NULL"
                if name in {"selected_schema_id", "selected_schema_version"}
                else f"source.{name}"
            )
            for name in DOCUMENT_COLUMNS
        )
        statement = (
            f"MERGE INTO {self._table} AS target USING (SELECT {source_fields}) AS source "
            "ON target.content_sha256 = source.content_sha256 "
            f"WHEN NOT MATCHED THEN INSERT ({insert_columns}) VALUES ({insert_values})"
        )
        values = dict(zip(DOCUMENT_COLUMNS, _document_values(document), strict=True))
        self.execute_sql(statement, {name: values[name] for name in parameter_names})

        registered = self.find_by_hash(document.content_sha256)
        if registered is None:
            raise RuntimeError("Document registry write did not produce a readable row")
        if registered.document_id != document.document_id:
            raise DuplicateDocumentError(registered)

    def list_documents(self) -> list[DocumentRecord]:
        rows = self.execute_sql(
            f"SELECT {', '.join(DOCUMENT_COLUMNS)} FROM {self._table} "
            "ORDER BY uploaded_at DESC, document_id DESC LIMIT 500"
        )
        return [_databricks_row_to_document(row) for row in rows]

    def get(self, document_id: str) -> DocumentRecord | None:
        rows = self.execute_sql(
            f"SELECT {', '.join(DOCUMENT_COLUMNS)} FROM {self._table} "
            "WHERE document_id = :document_id LIMIT 1",
            {"document_id": document_id},
        )
        return _databricks_row_to_document(rows[0]) if rows else None

    def update_status(
        self,
        document_id: str,
        expected_statuses: set[str],
        new_status: str,
    ) -> DocumentRecord:
        existing = self.get(document_id)
        if existing is None:
            raise KeyError(document_id)
        if existing.status not in expected_statuses:
            raise InvalidDocumentStateError(existing, expected_statuses)

        expected_markers = ", ".join(
            f":expected_status_{index}" for index, _ in enumerate(sorted(expected_statuses))
        )
        values: dict[str, object] = {
            "document_id": document_id,
            "new_status": new_status,
            "updated_at": datetime.now(UTC),
        }
        values.update(
            {
                f"expected_status_{index}": status
                for index, status in enumerate(sorted(expected_statuses))
            }
        )
        self.execute_sql(
            f"UPDATE {self._table} SET status = :new_status, "
            "updated_at = CAST(:updated_at AS TIMESTAMP) "
            f"WHERE document_id = :document_id AND status IN ({expected_markers})",
            values,
        )
        updated = self.get(document_id)
        if updated is None:
            raise RuntimeError("Document status update did not produce a readable row")
        if updated.status != new_status:
            raise InvalidDocumentStateError(updated, expected_statuses)
        return updated

    def execute_sql(
        self, statement: str, values: dict[str, object] | None = None
    ) -> list[list[str]]:
        parameters = [
            sql.StatementParameterListItem(name=name, value=_parameter_value(value))
            for name, value in (values or {}).items()
        ]
        response = self._client.statement_execution.execute_statement(
            statement=statement,
            warehouse_id=self._warehouse_id,
            catalog=self._catalog,
            schema=self._project_schema,
            parameters=parameters,
            wait_timeout="30s",
            on_wait_timeout=sql.ExecuteStatementRequestOnWaitTimeout.CONTINUE,
        )

        while response.status and response.status.state in {
            sql.StatementState.PENDING,
            sql.StatementState.RUNNING,
        }:
            if not response.statement_id:
                raise RuntimeError("Databricks SQL response did not include a statement identifier")
            time.sleep(0.25)
            response = self._client.statement_execution.get_statement(response.statement_id)

        if not response.status or response.status.state is not sql.StatementState.SUCCEEDED:
            message = (
                response.status.error.message
                if response.status and response.status.error
                else "Databricks SQL statement failed"
            )
            raise RuntimeError(message)
        if not response.result or not response.result.data_array:
            return []
        return cast(list[list[str]], response.result.data_array)


def _document_values(document: DocumentRecord) -> tuple[object, ...]:
    return (
        document.document_id,
        document.case_id,
        document.template_id,
        document.use_case,
        document.source_path,
        document.file_name,
        document.file_size,
        document.content_sha256,
        document.selected_schema_id,
        document.selected_schema_version,
        document.status,
        document.uploaded_by,
        document.uploaded_at.isoformat(),
        document.updated_at.isoformat(),
    )


def _sqlite_row_to_document(row: sqlite3.Row) -> DocumentRecord:
    return DocumentRecord(
        document_id=cast(str, row["document_id"]),
        case_id=cast(str | None, row["case_id"]),
        template_id=cast(str, row["template_id"]),
        use_case=cast(str, row["use_case"]),
        source_path=cast(str, row["source_path"]),
        file_name=cast(str, row["file_name"]),
        file_size=cast(int, row["file_size"]),
        content_sha256=cast(str, row["content_sha256"]),
        selected_schema_id=cast(str | None, row["selected_schema_id"]),
        selected_schema_version=cast(int | None, row["selected_schema_version"]),
        status=cast(str, row["status"]),
        uploaded_by=cast(str, row["uploaded_by"]),
        uploaded_at=datetime.fromisoformat(cast(str, row["uploaded_at"])),
        updated_at=datetime.fromisoformat(cast(str, row["updated_at"])),
    )


def _databricks_row_to_document(row: list[str]) -> DocumentRecord:
    values = dict(zip(DOCUMENT_COLUMNS, row, strict=True))
    return DocumentRecord(
        document_id=values["document_id"],
        case_id=values["case_id"] or None,
        template_id=values["template_id"],
        use_case=values["use_case"],
        source_path=values["source_path"],
        file_name=values["file_name"],
        file_size=int(values["file_size"]),
        content_sha256=values["content_sha256"],
        selected_schema_id=values["selected_schema_id"] or None,
        selected_schema_version=(
            int(values["selected_schema_version"])
            if values["selected_schema_version"]
            else None
        ),
        status=values["status"],
        uploaded_by=values["uploaded_by"],
        uploaded_at=_parse_timestamp(values["uploaded_at"]),
        updated_at=_parse_timestamp(values["updated_at"]),
    )


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parameter_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
