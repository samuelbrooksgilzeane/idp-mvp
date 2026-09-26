"""Shared durable work records for app requests, dispatchers and Job tasks."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from idp_app.services.batch_repository import DatabricksBatchRepository
from idp_app.services.document_registry import DatabricksDocumentRegistry


@dataclass(frozen=True)
class WorkItem:
    work_item_id: str
    document_id: str
    content_sha256: str
    parser_version: str
    parse_run_id: str
    requested_by: str
    execution_owner: str | None = None
    state: str = "QUEUED"
    kind: str = "PARSE"
    attempts: int = 0
    next_eligible_at: str = ""
    dispatch_id: str | None = None
    lease_expires_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    revision: int = 0
    transition_id: str = "initial"
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class WorkDispatch:
    dispatch_id: str
    work_item_ids: list[str]
    state: str = "PREPARING"
    kind: str = "PARSE"
    job_run_id: int | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    revision: int = 0
    transition_id: str = "initial"
    created_at: str = ""
    updated_at: str = ""


Record = TypeVar("Record", WorkItem, WorkDispatch)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def changed(record: Record, **changes: Any) -> Record:
    return replace(
        record,
        **changes,
        revision=record.revision + 1,
        transition_id=str(uuid4()),
        updated_at=now_iso(),
    )


class WorkRepository(Protocol):
    def put_item(self, item: WorkItem) -> WorkItem: ...
    def item(self, work_item_id: str) -> WorkItem | None: ...
    def candidates(self, limit: int) -> list[WorkItem]: ...
    def update_item(self, old: WorkItem, new: WorkItem) -> bool: ...
    def put_dispatch(self, dispatch: WorkDispatch) -> None: ...
    def dispatch(self, dispatch_id: str) -> WorkDispatch | None: ...
    def active_dispatches(self) -> list[WorkDispatch]: ...
    def summary(self) -> dict[str, Any]: ...
    def update_dispatch(self, old: WorkDispatch, new: WorkDispatch) -> bool: ...


class SQLiteWorkRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS work_items (
                    work_item_id TEXT PRIMARY KEY, state TEXT NOT NULL,
                    next_eligible_at TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS queued_work ON work_items(state, next_eligible_at);
                CREATE TABLE IF NOT EXISTS work_dispatches (
                    dispatch_id TEXT PRIMARY KEY, state TEXT NOT NULL,
                    revision INTEGER NOT NULL, payload TEXT NOT NULL
                );
            """)

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def put_item(self, item: WorkItem) -> WorkItem:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO work_items VALUES (?, ?, ?, ?, ?)",
                (
                    item.work_item_id,
                    item.state,
                    item.next_eligible_at,
                    item.revision,
                    json.dumps(asdict(item)),
                ),
            )
        saved = self.item(item.work_item_id)
        assert saved is not None
        return saved

    def item(self, work_item_id: str) -> WorkItem | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM work_items WHERE work_item_id = ?", (work_item_id,)
            ).fetchone()
        return WorkItem(**json.loads(row[0])) if row else None

    def candidates(self, limit: int) -> list[WorkItem]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM work_items WHERE state = 'QUEUED' "
                "AND next_eligible_at <= ? ORDER BY next_eligible_at, work_item_id LIMIT ?",
                (now_iso(), limit),
            ).fetchall()
        return [WorkItem(**json.loads(row[0])) for row in rows]

    def update_item(self, old: WorkItem, new: WorkItem) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE work_items SET state = ?, next_eligible_at = ?, revision = ?, payload = ? "
                "WHERE work_item_id = ? AND revision = ?",
                (
                    new.state,
                    new.next_eligible_at,
                    new.revision,
                    json.dumps(asdict(new)),
                    old.work_item_id,
                    old.revision,
                ),
            )
        return cursor.rowcount == 1

    def put_dispatch(self, dispatch: WorkDispatch) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO work_dispatches VALUES (?, ?, ?, ?)",
                (
                    dispatch.dispatch_id,
                    dispatch.state,
                    dispatch.revision,
                    json.dumps(asdict(dispatch)),
                ),
            )

    def dispatch(self, dispatch_id: str) -> WorkDispatch | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM work_dispatches WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
        return WorkDispatch(**json.loads(row[0])) if row else None

    def active_dispatches(self) -> list[WorkDispatch]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM work_dispatches "
                "WHERE state IN ('PREPARING', 'SUBMITTING', 'ASSIGNING', 'RUNNING') "
                "ORDER BY dispatch_id LIMIT 2"
            ).fetchall()
        return [WorkDispatch(**json.loads(row[0])) for row in rows]

    def summary(self) -> dict[str, Any]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*), "
                "MIN(json_extract(payload, '$.created_at')) FROM work_items GROUP BY state"
            ).fetchall()
        return {
            "counts": {row[0]: row[1] for row in rows},
            "oldest_queued_at": next((row[2] for row in rows if row[0] == "QUEUED"), None),
        }

    def update_dispatch(self, old: WorkDispatch, new: WorkDispatch) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE work_dispatches SET state = ?, revision = ?, "
                "payload = ? WHERE dispatch_id = ? AND revision = ?",
                (new.state, new.revision, json.dumps(asdict(new)), old.dispatch_id, old.revision),
            )
        return cursor.rowcount == 1


class DatabricksWorkRepository:
    def __init__(self, sql: DatabricksDocumentRegistry, namespace: str) -> None:
        self.sql = sql
        self.write = DatabricksBatchRepository(sql, namespace).write
        self.items_table = f"{namespace}_work_items"
        self.dispatches_table = f"{namespace}_work_dispatches"

    def put_item(self, item: WorkItem) -> WorkItem:
        self.write(
            f"MERGE INTO {self.items_table} t USING (SELECT :id AS work_item_id, "
            ":state AS state, :eligible AS next_eligible_at, 0 AS revision, :payload AS payload) s "
            "ON t.work_item_id = s.work_item_id WHEN NOT MATCHED THEN INSERT *",
            {
                "id": item.work_item_id,
                "state": item.state,
                "eligible": item.next_eligible_at,
                "payload": json.dumps(asdict(item)),
            },
        )
        saved = self.item(item.work_item_id)
        assert saved is not None
        return saved

    def item(self, work_item_id: str) -> WorkItem | None:
        rows = self.sql.execute_sql(
            f"SELECT payload FROM {self.items_table} WHERE work_item_id = :id LIMIT 2",
            {"id": work_item_id},
        )
        if len(rows) > 1:
            raise RuntimeError("Duplicate work identity requires reconciliation")
        return WorkItem(**json.loads(rows[0][0])) if rows else None

    def candidates(self, limit: int) -> list[WorkItem]:
        rows = self.sql.execute_sql(
            f"SELECT payload FROM {self.items_table} "
            "WHERE state = 'QUEUED' AND next_eligible_at <= :now "
            "ORDER BY next_eligible_at, work_item_id LIMIT CAST(:limit AS INT)",
            {"now": now_iso(), "limit": limit},
        )
        return [WorkItem(**json.loads(row[0])) for row in rows]

    def update_item(self, old: WorkItem, new: WorkItem) -> bool:
        self.write(
            f"UPDATE {self.items_table} SET state = :state, next_eligible_at = :eligible, "
            "revision = CAST(:next_revision AS INT), payload = :payload "
            "WHERE work_item_id = :id AND revision = CAST(:revision AS INT)",
            {
                "state": new.state,
                "eligible": new.next_eligible_at,
                "next_revision": new.revision,
                "payload": json.dumps(asdict(new)),
                "id": old.work_item_id,
                "revision": old.revision,
            },
        )
        saved = self.item(old.work_item_id)
        return saved is not None and saved.transition_id == new.transition_id

    def put_dispatch(self, dispatch: WorkDispatch) -> None:
        self.write(
            f"MERGE INTO {self.dispatches_table} t USING (SELECT :id AS dispatch_id, "
            ":state AS state, 0 AS revision, :payload AS payload) s "
            "ON t.dispatch_id = s.dispatch_id WHEN NOT MATCHED THEN INSERT *",
            {
                "id": dispatch.dispatch_id,
                "state": dispatch.state,
                "payload": json.dumps(asdict(dispatch)),
            },
        )

    def dispatch(self, dispatch_id: str) -> WorkDispatch | None:
        rows = self.sql.execute_sql(
            f"SELECT payload FROM {self.dispatches_table} WHERE dispatch_id = :id LIMIT 2",
            {"id": dispatch_id},
        )
        if len(rows) > 1:
            raise RuntimeError("Duplicate dispatch identity requires reconciliation")
        return WorkDispatch(**json.loads(rows[0][0])) if rows else None

    def active_dispatches(self) -> list[WorkDispatch]:
        rows = self.sql.execute_sql(
            f"SELECT payload FROM {self.dispatches_table} "
            "WHERE state IN ('PREPARING', 'SUBMITTING', 'ASSIGNING', 'RUNNING') "
            "ORDER BY dispatch_id LIMIT 2"
        )
        return [WorkDispatch(**json.loads(row[0])) for row in rows]

    def summary(self) -> dict[str, Any]:
        rows = self.sql.execute_sql(
            f"SELECT state, COUNT(*), "
            f"MIN(get_json_object(payload, '$.created_at')) FROM {self.items_table} GROUP BY state"
        )
        return {
            "counts": {row[0]: int(row[1]) for row in rows},
            "oldest_queued_at": next((row[2] for row in rows if row[0] == "QUEUED"), None),
        }

    def update_dispatch(self, old: WorkDispatch, new: WorkDispatch) -> bool:
        self.write(
            f"UPDATE {self.dispatches_table} SET state = :state, "
            "revision = CAST(:next_revision AS INT), payload = :payload "
            "WHERE dispatch_id = :id AND revision = CAST(:revision AS INT)",
            {
                "state": new.state,
                "next_revision": new.revision,
                "payload": json.dumps(asdict(new)),
                "id": old.dispatch_id,
                "revision": old.revision,
            },
        )
        saved = self.dispatch(old.dispatch_id)
        return saved is not None and saved.transition_id == new.transition_id
