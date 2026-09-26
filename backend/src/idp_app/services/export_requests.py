"""Pinned, owner-scoped export requests. Workers are serialized by the export Job."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from idp_app.services.batch_repository import DatabricksBatchRepository
from idp_app.services.documents import DocumentServiceError
from idp_app.services.export_writer import WRITER_VERSION


class ExportRequests:
    def __init__(self, path: Path | None = None, sql: Any = None, namespace: str = ""):
        self.path, self.sql, self.namespace = path, sql, namespace
        self.table = f"{namespace}_export_requests"
        self.members = f"{namespace}_export_members"
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path) as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS export_requests
                    (export_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS export_members
                    (export_id TEXT, ordinal INTEGER, extraction_run_id TEXT,
                     PRIMARY KEY(export_id, ordinal));
                """)

    def get(self, identity: str) -> dict[str, Any] | None:
        if self.path is not None:
            with sqlite3.connect(self.path) as conn:
                rows = conn.execute(
                    "SELECT payload FROM export_requests WHERE export_id=?", (identity,)
                ).fetchall()
        else:
            rows = self.sql.execute_sql(
                f"SELECT payload FROM {self.table} WHERE export_id=:id LIMIT 2", {"id": identity}
            )
        if len(rows) > 1:
            raise RuntimeError("Duplicate export identity requires reconciliation")
        return json.loads(rows[0][0]) if rows else None

    def owned(self, identity: str, user: str) -> dict[str, Any]:
        row = self.get(identity)
        if row is None or row["requester"] != user:
            raise DocumentServiceError("EXPORT_NOT_FOUND", "Export not found.", 404)
        if row["expires_at"] <= datetime.now(UTC).isoformat():
            row = {**row, "state": "EXPIRED"}
        return row

    def create(
        self, user: str, client_id: str, format: str, run_ids: list[str], retention_hours: int = 24
    ) -> dict[str, Any]:
        run_ids = list(dict.fromkeys(run_ids))
        if not 1 <= len(run_ids) <= 1000 or format not in {"xlsx", "csv"}:
            raise DocumentServiceError("INVALID_EXPORT", "Select 1–1,000 successful results.", 422)
        identity = str(
            uuid5(
                NAMESPACE_URL,
                json.dumps([str(self.path or self.namespace), user, client_id, "EXPORT"]),
            )
        )
        digest = hashlib.sha256(json.dumps([format, run_ids, WRITER_VERSION]).encode()).hexdigest()
        now = datetime.now(UTC)
        row = {
            "export_id": identity,
            "requester": user,
            "client_request_id": client_id,
            "selection_hash": digest,
            "writer_version": WRITER_VERSION,
            "format": format,
            "run_ids": run_ids,
            "selected_count": len(run_ids),
            "runs_processed": 0,
            "state": "QUEUED",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=retention_hours)).isoformat(),
            "error": None,
            "bytes": None,
            "filename": None,
        }
        if self.path is not None:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO export_requests VALUES (?, ?)",
                    (identity, json.dumps(row)),
                )
        else:
            self._write(
                f"MERGE INTO {self.table} t USING (SELECT :id export_id, :payload payload) s "
                "ON t.export_id=s.export_id WHEN NOT MATCHED THEN INSERT *",
                {"id": identity, "payload": json.dumps(row)},
            )
        saved = self.owned(identity, user)
        if saved["selection_hash"] != digest:
            raise DocumentServiceError(
                "EXPORT_REQUEST_CONFLICT", "Request ID already used with different inputs.", 409
            )
        self.repair_members(saved)
        return saved

    def repair_members(self, row: dict[str, Any]) -> None:
        # Header is the immutable authority. Repair a crash between the two Delta writes.
        members = [(row["export_id"], i, run) for i, run in enumerate(row["run_ids"])]
        if self.path is not None:
            with sqlite3.connect(self.path) as conn:
                conn.executemany("INSERT OR IGNORE INTO export_members VALUES (?, ?, ?)", members)
        else:
            payload = [
                dict(zip(("export_id", "ordinal", "extraction_run_id"), m, strict=True))
                for m in members
            ]
            self._write(
                f"MERGE INTO {self.members} t USING (SELECT item.* FROM (SELECT "
                "explode(from_json(:items, 'array<struct<export_id:string,ordinal:int,"
                "extraction_run_id:string>>')) item)) s "
                "ON t.export_id=s.export_id AND t.ordinal=s.ordinal "
                "WHEN NOT MATCHED THEN INSERT *",
                {"items": json.dumps(payload)},
            )

    def save(self, row: dict[str, Any]) -> None:
        # Only the single-concurrency Job writes lifecycle state; API reads never mutate it.
        if self.path is not None:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    "UPDATE export_requests SET payload=? WHERE export_id=?",
                    (json.dumps(row), row["export_id"]),
                )
        else:
            self._write(
                f"UPDATE {self.table} SET payload=:payload WHERE export_id=:id",
                {"payload": json.dumps(row), "id": row["export_id"]},
            )

    def _write(self, statement: str, params: dict[str, object]) -> None:
        DatabricksBatchRepository(self.sql, self.namespace).write(statement, params)
