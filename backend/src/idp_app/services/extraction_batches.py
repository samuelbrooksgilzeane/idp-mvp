"""Durable extraction requests, resolved in bounded steps outside HTTP submission.

A batch's pinned inputs are immutable once resolved. Job execution consumes only
READY batches; unresolved/partially persisted snapshots are never dispatchable.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

from idp_app.services.batch_repository import DatabricksBatchRepository
from idp_app.services.bulk_ids import MAX_WORK_BATCH_SIZE
from idp_app.services.document_registry import DatabricksDocumentRegistry, DocumentRegistry
from idp_app.services.documents import DocumentServiceError
from idp_app.services.extraction_inputs import published_schema, resolve_extraction_inputs
from idp_app.services.parse_runs import ParseRunRepository
from idp_app.services.schema_registry import SchemaRepository


@dataclass(frozen=True)
class ExtractionBatch:
    batch_id: str
    requested_by: str
    client_request_id: str
    manifest_hash: str
    document_ids: list[str]
    schema_id: str
    schema_version: int
    schema_hash: str
    created_at: str
    state: str = "VALIDATING"
    resolved: list[dict[str, Any]] = field(default_factory=list)
    retry_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    published: int = 0
    revision: int = 0
    transition_id: str = "initial"


class ExtractionBatchRepository(Protocol):
    def create(self, batch: ExtractionBatch) -> ExtractionBatch: ...
    def get(self, batch_id: str) -> ExtractionBatch | None: ...
    def pending(self, limit: int, state: str = "VALIDATING") -> list[ExtractionBatch]: ...
    def update(self, old: ExtractionBatch, new: ExtractionBatch) -> bool: ...


class SQLiteExtractionBatchRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS work_batches (
                batch_id TEXT PRIMARY KEY, state TEXT NOT NULL,
                revision INTEGER NOT NULL, payload TEXT NOT NULL)""")

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def create(self, batch: ExtractionBatch) -> ExtractionBatch:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO work_batches VALUES (?, ?, ?, ?)",
                (batch.batch_id, batch.state, batch.revision, json.dumps(asdict(batch))),
            )
        saved = self.get(batch.batch_id)
        assert saved is not None
        return saved

    def get(self, batch_id: str) -> ExtractionBatch | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM work_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
        return ExtractionBatch(**json.loads(row[0])) if row else None

    def pending(self, limit: int, state: str = "VALIDATING") -> list[ExtractionBatch]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM work_batches WHERE state = ? ORDER BY batch_id LIMIT ?",
                (state, limit),
            ).fetchall()
        return [ExtractionBatch(**json.loads(row[0])) for row in rows]

    def update(self, old: ExtractionBatch, new: ExtractionBatch) -> bool:
        with self.connect() as connection:
            result = connection.execute(
                "UPDATE work_batches SET state = ?, revision = ?, payload = ? "
                "WHERE batch_id = ? AND revision = ?",
                (new.state, new.revision, json.dumps(asdict(new)), old.batch_id, old.revision),
            )
        return result.rowcount == 1


class DatabricksExtractionBatchRepository:
    def __init__(self, sql: DatabricksDocumentRegistry, namespace: str) -> None:
        self.sql = sql
        self.table = f"{namespace}_work_batches"
        self.write = DatabricksBatchRepository(sql, namespace).write

    def create(self, batch: ExtractionBatch) -> ExtractionBatch:
        self.write(
            f"MERGE INTO {self.table} t USING (SELECT :id AS batch_id, "
            ":state AS state, 0 AS revision, :payload AS payload) s "
            "ON t.batch_id = s.batch_id WHEN NOT MATCHED THEN INSERT *",
            {"id": batch.batch_id, "state": batch.state, "payload": json.dumps(asdict(batch))},
        )
        saved = self.get(batch.batch_id)
        assert saved is not None
        return saved

    def get(self, batch_id: str) -> ExtractionBatch | None:
        rows = self.sql.execute_sql(
            f"SELECT payload FROM {self.table} WHERE batch_id = :id LIMIT 2", {"id": batch_id}
        )
        if len(rows) > 1:
            raise RuntimeError("Duplicate extraction batch identity requires reconciliation")
        return ExtractionBatch(**json.loads(rows[0][0])) if rows else None

    def pending(self, limit: int, state: str = "VALIDATING") -> list[ExtractionBatch]:
        rows = self.sql.execute_sql(
            f"SELECT payload FROM {self.table} WHERE state = :state "
            "ORDER BY batch_id LIMIT CAST(:limit AS INT)",
            {"limit": limit, "state": state},
        )
        return [ExtractionBatch(**json.loads(row[0])) for row in rows]

    def update(self, old: ExtractionBatch, new: ExtractionBatch) -> bool:
        self.write(
            f"UPDATE {self.table} SET state = :state, revision = CAST(:next AS INT), "
            "payload = :payload WHERE batch_id = :id AND revision = CAST(:revision AS INT)",
            {
                "state": new.state,
                "next": new.revision,
                "payload": json.dumps(asdict(new)),
                "id": old.batch_id,
                "revision": old.revision,
            },
        )
        saved = self.get(old.batch_id)
        return saved is not None and saved.transition_id == new.transition_id


class ExtractionBatchService:
    def __init__(
        self,
        batches: ExtractionBatchRepository,
        documents: DocumentRegistry,
        parses: ParseRunRepository,
        schemas: SchemaRepository,
        project: str,
    ) -> None:
        self.batches, self.documents, self.parses, self.schemas = (
            batches,
            documents,
            parses,
            schemas,
        )
        self.project = project

    def create(
        self,
        document_ids: list[str],
        schema_id: str,
        schema_version: int,
        requested_by: str,
        client_request_id: str,
    ) -> ExtractionBatch:
        identities = list(dict.fromkeys(document_ids))
        if not identities or len(identities) > MAX_WORK_BATCH_SIZE:
            raise DocumentServiceError(
                "INVALID_BATCH_SIZE", "Select between 1 and 1,000 documents.", 422
            )
        if not client_request_id or len(client_request_id) > 128:
            raise DocumentServiceError(
                "INVALID_REQUEST_ID", "A bounded request identity is required.", 422
            )
        identity = str(
            uuid5(
                NAMESPACE_URL,
                json.dumps([self.project, requested_by, client_request_id, "EXTRACT"]),
            )
        )
        digest = hashlib.sha256(
            json.dumps([identities, schema_id, schema_version]).encode()
        ).hexdigest()
        existing = self.batches.get(identity)
        if existing is not None:
            return self._replay(existing, requested_by, digest)
        # One template lookup; document validation belongs to the coordinator.
        schema = published_schema(self.schemas, schema_id, schema_version)
        batch = ExtractionBatch(
            identity,
            requested_by,
            client_request_id,
            digest,
            identities,
            schema_id,
            schema_version,
            schema.schema_hash,
            datetime.now(UTC).isoformat(),
        )
        return self._replay(self.batches.create(batch), requested_by, digest)

    @staticmethod
    def _replay(batch: ExtractionBatch, requester: str, digest: str) -> ExtractionBatch:
        if batch.requested_by != requester or batch.manifest_hash != digest:
            raise DocumentServiceError(
                "BATCH_REQUEST_CONFLICT", "Request identity already used for different inputs.", 409
            )
        return batch

    def owned(self, batch_id: str, requester: str) -> ExtractionBatch:
        batch = self.batches.get(batch_id)
        if batch is None or batch.requested_by != requester:
            raise DocumentServiceError("BATCH_NOT_FOUND", "Extraction batch not found.", 404)
        return batch

    def resolve_step(self, batch_id: str, limit: int = 100) -> ExtractionBatch:
        if not 1 <= limit <= 100:
            raise ValueError("Resolve steps must contain between 1 and 100 documents")
        batch = self.batches.get(batch_id)
        if batch is None:
            raise KeyError(batch_id)
        if batch.state != "VALIDATING":
            return batch
        offset = len(batch.resolved)
        chunk = batch.document_ids[offset : offset + limit]
        schema = self.schemas.get(batch.schema_id, batch.schema_version)
        members: list[dict[str, Any]]
        if (
            schema is None
            or schema.schema_hash != batch.schema_hash
            or schema.status not in {"PUBLISHED", "PRODUCTION"}
        ):
            members = [
                {
                    "document_id": identity,
                    "state": "FAILED",
                    "error_code": "SCHEMA_CHANGED",
                    "error_message": "The pinned template is unavailable or changed.",
                }
                for identity in chunk
            ]
        else:
            fresh = [identity for identity in chunk if identity not in batch.retry_inputs]
            inputs, failures = resolve_extraction_inputs(self.documents, self.parses, schema, fresh)
            by_id: dict[str, dict[str, Any]] = {
                item.document.document_id: {
                    "document_id": item.document.document_id,
                    "file_name": item.document.file_name,
                    "content_sha256": item.document.content_sha256,
                    "parse_run_id": item.parse.parse_run_id,
                    "state": "QUEUED",
                }
                for item in inputs
            }
            by_id.update(
                {
                    failure.document_id: {
                        "document_id": failure.document_id,
                        "state": "FAILED",
                        "error_code": failure.code,
                        "error_message": failure.message,
                    }
                    for failure in failures
                }
            )
            by_id.update(
                {
                    identity: {
                        **batch.retry_inputs[identity],
                        "state": "QUEUED",
                        "error_code": None,
                        "error_message": None,
                    }
                    for identity in chunk
                    if identity in batch.retry_inputs
                }
            )
            members = [by_id[identity] for identity in chunk]
        for ordinal, member in enumerate(members, start=offset):
            member["ordinal"] = ordinal
            member["work_item_id"] = str(uuid5(NAMESPACE_URL, f"{batch.batch_id}/{ordinal}"))
            member["extraction_run_id"] = str(
                uuid5(NAMESPACE_URL, f"{batch.batch_id}/{ordinal}/initial")
            )
        resolved = [*batch.resolved, *members]
        new = replace(
            batch,
            resolved=resolved,
            revision=batch.revision + 1,
            transition_id=str(uuid4()),
            state="READY" if len(resolved) == len(batch.document_ids) else "VALIDATING",
        )
        self.batches.update(batch, new)
        # Reload the CAS winner: an overlapping recovery step must never replace its pinned inputs.
        saved = self.batches.get(batch_id)
        assert saved is not None
        return saved
