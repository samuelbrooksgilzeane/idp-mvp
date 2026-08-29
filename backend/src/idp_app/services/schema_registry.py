from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from idp_app.services.document_registry import DatabricksDocumentRegistry
from idp_app.services.schema_models import (
    DocumentRule,
    ExtractField,
    FieldPolicy,
    SchemaManifest,
    SchemaRecord,
)

SCHEMA_COLUMNS = (
    "schema_id",
    "schema_version",
    "display_name",
    "use_case",
    "ai_extract_schema_json",
    "instructions",
    "field_policy_json",
    "document_rule_json",
    "schema_hash",
    "status",
    "created_by",
    "created_at",
)


class SchemaVersionConflictError(Exception):
    pass


class SchemaRepository(Protocol):
    def register(self, manifest: SchemaManifest, created_by: str) -> SchemaRecord: ...

    def list(self, status: str, use_case: str | None) -> list[SchemaRecord]: ...

    def get(self, schema_id: str, schema_version: int) -> SchemaRecord | None: ...


class SQLiteSchemaRepository:
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
                CREATE TABLE IF NOT EXISTS schema_registry (
                    schema_id TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    display_name TEXT NOT NULL,
                    use_case TEXT NOT NULL,
                    ai_extract_schema_json TEXT NOT NULL,
                    instructions TEXT NOT NULL,
                    field_policy_json TEXT NOT NULL,
                    document_rule_json TEXT NOT NULL,
                    schema_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (schema_id, schema_version)
                )
                """
            )

    def register(self, manifest: SchemaManifest, created_by: str) -> SchemaRecord:
        existing = self.get(manifest.schema_id, manifest.schema_version)
        if existing is not None:
            _verify_immutable(existing, manifest)
            return existing

        created_at = datetime.now(UTC)
        values = _manifest_values(manifest, created_by, created_at)
        try:
            with self._connect() as connection:
                connection.execute(
                    f"INSERT INTO schema_registry ({', '.join(SCHEMA_COLUMNS)}) "
                    f"VALUES ({', '.join('?' for _ in SCHEMA_COLUMNS)})",
                    values,
                )
        except sqlite3.IntegrityError:
            concurrent = self.get(manifest.schema_id, manifest.schema_version)
            if concurrent is None:
                raise
            _verify_immutable(concurrent, manifest)
            return concurrent

        registered = self.get(manifest.schema_id, manifest.schema_version)
        if registered is None:
            raise RuntimeError("Schema registration did not produce a readable row")
        return registered

    def list(self, status: str, use_case: str | None) -> list[SchemaRecord]:
        statement = "SELECT * FROM schema_registry WHERE status = ?"
        parameters: tuple[object, ...] = (status,)
        if use_case is not None:
            statement += " AND use_case = ?"
            parameters += (use_case,)
        statement += " ORDER BY schema_id, schema_version DESC"
        with self._connect() as connection:
            rows = connection.execute(statement, parameters).fetchall()
        return [_sqlite_row_to_record(row) for row in rows]

    def get(self, schema_id: str, schema_version: int) -> SchemaRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM schema_registry "
                "WHERE schema_id = ? AND schema_version = ? LIMIT 1",
                (schema_id, schema_version),
            ).fetchone()
        return _sqlite_row_to_record(row) if row else None


class DatabricksSchemaRepository:
    def __init__(
        self,
        sql_client: DatabricksDocumentRegistry,
        catalog: str,
        project_schema: str,
        table_prefix: str,
    ) -> None:
        self._sql_client = sql_client
        self._table = f"{catalog}.{project_schema}.{table_prefix}_schema_registry"

    def register(self, manifest: SchemaManifest, created_by: str) -> SchemaRecord:
        existing = self.get(manifest.schema_id, manifest.schema_version)
        if existing is not None:
            _verify_immutable(existing, manifest)
            return existing

        values = dict(
            zip(
                SCHEMA_COLUMNS,
                _manifest_values(manifest, created_by, datetime.now(UTC)),
                strict=True,
            )
        )
        source = ", ".join(f":{column} AS {column}" for column in SCHEMA_COLUMNS)
        insert_values = ", ".join(f"source.{column}" for column in SCHEMA_COLUMNS)
        self._sql_client.execute_sql(
            f"MERGE INTO {self._table} AS target USING (SELECT {source}) AS source "
            "ON target.schema_id = source.schema_id "
            "AND target.schema_version = source.schema_version "
            f"WHEN NOT MATCHED THEN INSERT ({', '.join(SCHEMA_COLUMNS)}) "
            f"VALUES ({insert_values})",
            values,
        )
        registered = self.get(manifest.schema_id, manifest.schema_version)
        if registered is None:
            raise RuntimeError("Schema registration did not produce a readable row")
        _verify_immutable(registered, manifest)
        return registered

    def list(self, status: str, use_case: str | None) -> list[SchemaRecord]:
        statement = (
            f"SELECT {', '.join(SCHEMA_COLUMNS)} FROM {self._table} "
            "WHERE status = :status"
        )
        values: dict[str, object] = {"status": status}
        if use_case is not None:
            statement += " AND use_case = :use_case"
            values["use_case"] = use_case
        statement += " ORDER BY schema_id, schema_version DESC"
        return [
            _databricks_row_to_record(row)
            for row in self._sql_client.execute_sql(statement, values)
        ]

    def get(self, schema_id: str, schema_version: int) -> SchemaRecord | None:
        rows = self._sql_client.execute_sql(
            f"SELECT {', '.join(SCHEMA_COLUMNS)} FROM {self._table} "
            "WHERE schema_id = :schema_id AND schema_version = :schema_version LIMIT 1",
            {"schema_id": schema_id, "schema_version": schema_version},
        )
        return _databricks_row_to_record(rows[0]) if rows else None


def _manifest_values(
    manifest: SchemaManifest,
    created_by: str,
    created_at: datetime,
) -> tuple[object, ...]:
    return (
        manifest.schema_id,
        manifest.schema_version,
        manifest.display_name,
        manifest.use_case,
        manifest.ai_extract_schema_json,
        manifest.instructions,
        _canonical_model_json(manifest.field_policies),
        _canonical_model_json(manifest.document_rules),
        manifest.schema_hash,
        manifest.status,
        created_by,
        created_at.isoformat(),
    )


def _canonical_model_json(value: object) -> str:
    serializable: object
    if isinstance(value, dict):
        serializable = {
            key: item.model_dump(mode="json") if isinstance(item, FieldPolicy) else item
            for key, item in value.items()
        }
    elif isinstance(value, list):
        serializable = [
            item.model_dump(mode="json") if isinstance(item, DocumentRule) else item
            for item in value
        ]
    else:
        serializable = value
    return json.dumps(serializable, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _verify_immutable(existing: SchemaRecord, manifest: SchemaManifest) -> None:
    if existing.schema_hash != manifest.schema_hash:
        raise SchemaVersionConflictError(
            f"Schema {manifest.schema_id} version {manifest.schema_version} is immutable"
        )


def _sqlite_row_to_record(row: sqlite3.Row) -> SchemaRecord:
    values = {column: row[column] for column in SCHEMA_COLUMNS}
    return _values_to_record(values)


def _databricks_row_to_record(row: list[str]) -> SchemaRecord:
    return _values_to_record(dict(zip(SCHEMA_COLUMNS, row, strict=True)))


def _values_to_record(values: dict[str, object]) -> SchemaRecord:
    extract_schema_raw = cast(
        dict[str, object],
        json.loads(cast(str, values["ai_extract_schema_json"])),
    )
    field_policy_raw = cast(dict[str, object], json.loads(cast(str, values["field_policy_json"])))
    document_rule_raw = cast(list[object], json.loads(cast(str, values["document_rule_json"])))
    return SchemaRecord(
        schema_id=cast(str, values["schema_id"]),
        schema_version=int(cast(str | int, values["schema_version"])),
        display_name=cast(str, values["display_name"]),
        use_case=cast(str, values["use_case"]),
        ai_extract_schema={
            name: ExtractField.model_validate(definition)
            for name, definition in extract_schema_raw.items()
        },
        instructions=cast(str, values["instructions"]),
        field_policies={
            name: FieldPolicy.model_validate(policy)
            for name, policy in field_policy_raw.items()
        },
        document_rules=[DocumentRule.model_validate(rule) for rule in document_rule_raw],
        schema_hash=cast(str, values["schema_hash"]),
        status=cast(str, values["status"]),
        created_by=cast(str, values["created_by"]),
        created_at=datetime.fromisoformat(cast(str, values["created_at"])),
    )
