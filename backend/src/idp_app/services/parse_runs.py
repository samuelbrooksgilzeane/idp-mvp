from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from idp_app.services.document_models import ParseRunRecord
from idp_app.services.document_registry import DatabricksDocumentRegistry

PARSE_RUN_COLUMNS = (
    "parse_run_id",
    "document_id",
    "content_sha256",
    "parser_version",
    "parsed",
    "document_text",
    "page_count",
    "page_image_root",
    "parse_error",
    "status",
    "requested_by",
    "job_run_id",
    "started_at",
    "completed_at",
)


class ParseRunRepository(Protocol):
    def create(self, run: ParseRunRecord) -> None: ...

    def assign_job_run(self, parse_run_id: str, job_run_id: int) -> None: ...

    def complete(
        self,
        parse_run_id: str,
        parsed: dict[str, Any],
        document_text: str,
        page_count: int,
    ) -> None: ...

    def fail(
        self,
        parse_run_id: str,
        parse_error: dict[str, Any] | list[Any],
    ) -> None: ...

    def get(self, parse_run_id: str) -> ParseRunRecord | None: ...

    def list_for_document(self, document_id: str) -> list[ParseRunRecord]: ...

    def list_for_job_run(self, job_run_id: int) -> list[ParseRunRecord]: ...

    def latest_successful(self, document_id: str) -> ParseRunRecord | None: ...


class SQLiteParseRunRepository:
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
                CREATE TABLE IF NOT EXISTS parse_runs (
                    parse_run_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    parsed TEXT,
                    document_text TEXT,
                    page_count INTEGER,
                    page_image_root TEXT NOT NULL,
                    parse_error TEXT,
                    status TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    job_run_id INTEGER,
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS parse_runs_document_history
                ON parse_runs (document_id, started_at DESC, parse_run_id DESC)
                """
            )

    def create(self, run: ParseRunRecord) -> None:
        values = _parse_run_values(run)
        placeholders = ", ".join("?" for _ in PARSE_RUN_COLUMNS)
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO parse_runs ({', '.join(PARSE_RUN_COLUMNS)}) "
                f"VALUES ({placeholders})",
                values,
            )

    def assign_job_run(self, parse_run_id: str, job_run_id: int) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE parse_runs SET job_run_id = ? "
                "WHERE parse_run_id = ? AND status = 'RUNNING'",
                (job_run_id, parse_run_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Parse run is not available for job assignment")

    def complete(
        self,
        parse_run_id: str,
        parsed: dict[str, Any],
        document_text: str,
        page_count: int,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE parse_runs
                SET parsed = ?, document_text = ?, page_count = ?, parse_error = NULL,
                    status = 'SUCCESS', completed_at = ?
                WHERE parse_run_id = ? AND status = 'RUNNING'
                """,
                (
                    json.dumps(parsed, separators=(",", ":")),
                    document_text,
                    page_count,
                    datetime.now(UTC).isoformat(),
                    parse_run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Parse run is not eligible for completion")

    def fail(
        self,
        parse_run_id: str,
        parse_error: dict[str, Any] | list[Any],
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE parse_runs
                SET parse_error = ?, status = 'FAILED', completed_at = ?
                WHERE parse_run_id = ? AND status = 'RUNNING'
                """,
                (
                    json.dumps(parse_error, separators=(",", ":")),
                    datetime.now(UTC).isoformat(),
                    parse_run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Parse run is not eligible for failure completion")

    def get(self, parse_run_id: str) -> ParseRunRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM parse_runs WHERE parse_run_id = ? LIMIT 1",
                (parse_run_id,),
            ).fetchone()
        return _sqlite_row_to_parse_run(row) if row else None

    def list_for_document(self, document_id: str) -> list[ParseRunRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM parse_runs WHERE document_id = ? "
                "ORDER BY started_at DESC, parse_run_id DESC",
                (document_id,),
            ).fetchall()
        return [_sqlite_row_to_parse_run(row) for row in rows]

    def list_for_job_run(self, job_run_id: int) -> list[ParseRunRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM parse_runs WHERE job_run_id = ? ORDER BY started_at, parse_run_id",
                (job_run_id,),
            ).fetchall()
        return [_sqlite_row_to_parse_run(row) for row in rows]

    def latest_successful(self, document_id: str) -> ParseRunRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM parse_runs WHERE document_id = ? AND status = 'SUCCESS' "
                "ORDER BY completed_at DESC, parse_run_id DESC LIMIT 1",
                (document_id,),
            ).fetchone()
        return _sqlite_row_to_parse_run(row) if row else None


class DatabricksParseRunRepository:
    def __init__(
        self,
        sql_registry: DatabricksDocumentRegistry,
        catalog: str,
        project_schema: str,
        table_prefix: str,
    ) -> None:
        self._sql = sql_registry
        self._table = f"{catalog}.{project_schema}.{table_prefix}_parsed_documents"

    def create(self, run: ParseRunRecord) -> None:
        self._sql.execute_sql(
            f"INSERT INTO {self._table} "
            "(parse_run_id, document_id, content_sha256, parser_version, "
            "parsed, document_text, page_count, page_image_root, parse_error, status, "
            "requested_by, job_run_id, started_at, completed_at) VALUES "
            "(:parse_run_id, :document_id, :content_sha256, :parser_version, NULL, NULL, NULL, "
            ":page_image_root, NULL, 'RUNNING', :requested_by, NULL, "
            "CAST(:started_at AS TIMESTAMP), NULL)",
            {
                "parse_run_id": run.parse_run_id,
                "document_id": run.document_id,
                "content_sha256": run.content_sha256,
                "parser_version": run.parser_version,
                "page_image_root": run.page_image_root,
                "requested_by": run.requested_by,
                "started_at": run.started_at,
            },
        )

    def assign_job_run(self, parse_run_id: str, job_run_id: int) -> None:
        self._sql.execute_sql(
            f"UPDATE {self._table} SET job_run_id = CAST(:job_run_id AS BIGINT) "
            "WHERE parse_run_id = :parse_run_id AND status = 'RUNNING'",
            {"parse_run_id": parse_run_id, "job_run_id": job_run_id},
        )

    def complete(
        self,
        parse_run_id: str,
        parsed: dict[str, Any],
        document_text: str,
        page_count: int,
    ) -> None:
        self._sql.execute_sql(
            f"UPDATE {self._table} SET parsed = PARSE_JSON(:parsed), "
            "document_text = :document_text, page_count = CAST(:page_count AS INT), "
            "parse_error = NULL, status = 'SUCCESS', completed_at = CURRENT_TIMESTAMP() "
            "WHERE parse_run_id = :parse_run_id AND status = 'RUNNING'",
            {
                "parse_run_id": parse_run_id,
                "parsed": json.dumps(parsed, separators=(",", ":")),
                "document_text": document_text,
                "page_count": page_count,
            },
        )

    def fail(
        self,
        parse_run_id: str,
        parse_error: dict[str, Any] | list[Any],
    ) -> None:
        self._sql.execute_sql(
            f"UPDATE {self._table} SET parse_error = PARSE_JSON(:parse_error), "
            "status = 'FAILED', completed_at = CURRENT_TIMESTAMP() "
            "WHERE parse_run_id = :parse_run_id AND status = 'RUNNING'",
            {
                "parse_run_id": parse_run_id,
                "parse_error": json.dumps(parse_error, separators=(",", ":")),
            },
        )

    def get(self, parse_run_id: str) -> ParseRunRecord | None:
        rows = self._sql.execute_sql(
            self._select_sql() + " WHERE parse_run_id = :parse_run_id LIMIT 1",
            {"parse_run_id": parse_run_id},
        )
        return _databricks_row_to_parse_run(rows[0]) if rows else None

    def list_for_document(self, document_id: str) -> list[ParseRunRecord]:
        rows = self._sql.execute_sql(
            self._select_sql()
            + " WHERE document_id = :document_id "
            "ORDER BY started_at DESC, parse_run_id DESC LIMIT 100",
            {"document_id": document_id},
        )
        return [_databricks_row_to_parse_run(row) for row in rows]

    def list_for_job_run(self, job_run_id: int) -> list[ParseRunRecord]:
        rows = self._sql.execute_sql(
            self._select_sql()
            + " WHERE job_run_id = :job_run_id ORDER BY started_at, parse_run_id LIMIT 500",
            {"job_run_id": job_run_id},
        )
        return [_databricks_row_to_parse_run(row) for row in rows]

    def latest_successful(self, document_id: str) -> ParseRunRecord | None:
        rows = self._sql.execute_sql(
            self._select_sql()
            + " WHERE document_id = :document_id AND status = 'SUCCESS' "
            "ORDER BY completed_at DESC, parse_run_id DESC LIMIT 1",
            {"document_id": document_id},
        )
        return _databricks_row_to_parse_run(rows[0]) if rows else None

    def _select_sql(self) -> str:
        return (
            "SELECT parse_run_id, document_id, content_sha256, parser_version, TO_JSON(parsed), "
            "document_text, page_count, page_image_root, TO_JSON(parse_error), status, "
            f"requested_by, job_run_id, started_at, completed_at FROM {self._table}"
        )


def _parse_run_values(run: ParseRunRecord) -> tuple[object, ...]:
    return (
        run.parse_run_id,
        run.document_id,
        run.content_sha256,
        run.parser_version,
        json.dumps(run.parsed, separators=(",", ":")) if run.parsed else None,
        run.document_text,
        run.page_count,
        run.page_image_root,
        json.dumps(run.parse_error, separators=(",", ":")) if run.parse_error else None,
        run.status,
        run.requested_by,
        run.job_run_id,
        run.started_at.isoformat(),
        run.completed_at.isoformat() if run.completed_at else None,
    )


def _sqlite_row_to_parse_run(row: sqlite3.Row) -> ParseRunRecord:
    return ParseRunRecord(
        parse_run_id=cast(str, row["parse_run_id"]),
        document_id=cast(str, row["document_id"]),
        content_sha256=cast(str, row["content_sha256"]),
        parser_version=cast(str, row["parser_version"]),
        parsed=json.loads(row["parsed"]) if row["parsed"] else None,
        document_text=cast(str | None, row["document_text"]),
        page_count=cast(int | None, row["page_count"]),
        page_image_root=cast(str, row["page_image_root"]),
        parse_error=json.loads(row["parse_error"]) if row["parse_error"] else None,
        status=cast(str, row["status"]),
        requested_by=cast(str, row["requested_by"]),
        job_run_id=cast(int | None, row["job_run_id"]),
        started_at=datetime.fromisoformat(cast(str, row["started_at"])),
        completed_at=(
            datetime.fromisoformat(cast(str, row["completed_at"]))
            if row["completed_at"]
            else None
        ),
    )


def _databricks_row_to_parse_run(row: list[str]) -> ParseRunRecord:
    values = dict(zip(PARSE_RUN_COLUMNS, row, strict=True))
    return ParseRunRecord(
        parse_run_id=values["parse_run_id"],
        document_id=values["document_id"],
        content_sha256=values["content_sha256"],
        parser_version=values["parser_version"],
        parsed=json.loads(values["parsed"]) if values["parsed"] else None,
        document_text=values["document_text"] or None,
        page_count=int(values["page_count"]) if values["page_count"] else None,
        page_image_root=values["page_image_root"],
        parse_error=json.loads(values["parse_error"]) if values["parse_error"] else None,
        status=values["status"],
        requested_by=values["requested_by"],
        job_run_id=int(values["job_run_id"]) if values["job_run_id"] else None,
        started_at=_parse_timestamp(values["started_at"]),
        completed_at=(
            _parse_timestamp(values["completed_at"]) if values["completed_at"] else None
        ),
    )


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
