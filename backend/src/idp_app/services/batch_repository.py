"""Durable upload metadata. Item updates use owner tokens, never browser locks."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Protocol

from idp_app.services.document_registry import DatabricksDocumentRegistry
from idp_app.services.sql_retry import RetryOutcome, run_with_retries


class BatchRepository(Protocol):
    def create(self, header: dict[str, Any], items: list[dict[str, Any]]) -> None: ...
    def header(self, batch_id: str) -> dict[str, Any] | None: ...
    def items(self, batch_id: str, after: int, limit: int) -> list[dict[str, Any]]: ...
    def item(self, batch_id: str, client_file_id: str) -> dict[str, Any] | None: ...
    def header_and_item(
        self, batch_id: str, client_file_id: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]: ...
    def counts(self, batch_id: str) -> dict[str, int]: ...
    def compare_and_set(self, old: dict[str, Any], new: dict[str, Any]) -> bool: ...


class SQLiteBatchRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS upload_batches (
                    batch_id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS upload_items (
                    batch_id TEXT NOT NULL, client_file_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL, state TEXT NOT NULL,
                    revision INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (batch_id, client_file_id)
                );
                CREATE INDEX IF NOT EXISTS upload_items_page ON upload_items(batch_id, ordinal);
            """)

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def create(self, header: dict[str, Any], items: list[dict[str, Any]]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO upload_batches VALUES (?, ?)",
                (header["batch_id"], json.dumps(header)),
            )
            saved = connection.execute(
                "SELECT payload FROM upload_batches WHERE batch_id = ?", (header["batch_id"],)
            ).fetchone()
            if json.loads(saved[0])["manifest_hash"] != header["manifest_hash"]:
                return
            connection.executemany(
                "INSERT OR IGNORE INTO upload_items VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        item["batch_id"],
                        item["client_file_id"],
                        item["ordinal"],
                        item["state"],
                        item["revision"],
                        json.dumps(item),
                    )
                    for item in items
                ],
            )

    def header(self, batch_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM upload_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def items(self, batch_id: str, after: int, limit: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM upload_items WHERE batch_id = ? AND ordinal > ? "
                "ORDER BY ordinal LIMIT ?",
                (batch_id, after, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def item(self, batch_id: str, client_file_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM upload_items WHERE batch_id = ? AND client_file_id = ?",
                (batch_id, client_file_id),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def header_and_item(
        self, batch_id: str, client_file_id: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT h.payload, i.payload FROM upload_batches h LEFT JOIN upload_items i "
                "ON i.batch_id = h.batch_id AND i.client_file_id = ? WHERE h.batch_id = ?",
                (client_file_id, batch_id),
            ).fetchone()
        if row is None:
            return None, None
        return json.loads(row[0]), json.loads(row[1]) if row[1] else None

    def counts(self, batch_id: str) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) FROM upload_items WHERE batch_id = ? GROUP BY state",
                (batch_id,),
            ).fetchall()
        return dict(rows)

    def compare_and_set(self, old: dict[str, Any], new: dict[str, Any]) -> bool:
        with self.connect() as connection:
            result = connection.execute(
                "UPDATE upload_items SET state = ?, revision = ?, payload = ? "
                "WHERE batch_id = ? AND client_file_id = ? AND revision = ?",
                (
                    new["state"],
                    new["revision"],
                    json.dumps(new),
                    old["batch_id"],
                    old["client_file_id"],
                    old["revision"],
                ),
            )
        return result.rowcount == 1


class DatabricksBatchRepository:
    def __init__(self, sql: DatabricksDocumentRegistry, namespace: str) -> None:
        self.sql = sql
        self.headers = f"{namespace}_upload_batches"
        self.members = f"{namespace}_upload_items"

    def write(
        self, statement: str, values: dict[str, object], outcome: RetryOutcome | None = None
    ) -> dict[str, int]:
        """Run one DML statement and return its row counts (empty when none were reported).

        Every write here is safe to repeat (idempotent MERGE or revision-checked UPDATE), so Delta
        conflicts and transient warehouse failures are retried a bounded number of times."""
        return run_with_retries(
            lambda: self.sql.execute_dml(statement, values), outcome, label="upload metadata write"
        )

    def create(self, header: dict[str, Any], items: list[dict[str, Any]]) -> None:
        self.write(
            f"MERGE INTO {self.headers} t USING (SELECT :batch_id AS batch_id, "
            ":payload AS payload) s ON t.batch_id = s.batch_id "
            "WHEN NOT MATCHED THEN INSERT *",
            {"batch_id": header["batch_id"], "payload": json.dumps(header)},
        )
        saved = self.header(header["batch_id"])
        if saved is None or saved["manifest_hash"] != header["manifest_hash"]:
            return
        # One bounded bulk write. Retrying a partial initialization repairs missing members.
        payload = [
            {
                "batch_id": item["batch_id"],
                "client_file_id": item["client_file_id"],
                "ordinal": item["ordinal"],
                "state": item["state"],
                "revision": item["revision"],
                "payload": json.dumps(item),
            }
            for item in items
        ]
        self.write(
            f"MERGE INTO {self.members} t USING (SELECT item.* FROM "
            "(SELECT explode(from_json(:items, 'array<struct<batch_id:string,"
            "client_file_id:string,ordinal:int,state:string,revision:int,payload:string>>')) "
            "item)) s "
            "ON t.batch_id = s.batch_id AND t.client_file_id = s.client_file_id "
            "WHEN NOT MATCHED THEN INSERT *",
            {"items": json.dumps(payload)},
        )

    def header(self, batch_id: str) -> dict[str, Any] | None:
        rows = self.sql.execute_sql(
            f"SELECT payload FROM {self.headers} WHERE batch_id = :batch_id LIMIT 2",
            {"batch_id": batch_id},
        )
        if len(rows) > 1:
            raise RuntimeError("Duplicate batch identity requires reconciliation")
        return json.loads(rows[0][0]) if rows else None

    def items(self, batch_id: str, after: int, limit: int) -> list[dict[str, Any]]:
        rows = self.sql.execute_sql(
            f"SELECT payload FROM {self.members} WHERE batch_id = :batch_id "
            "AND ordinal > CAST(:after AS INT) ORDER BY ordinal LIMIT CAST(:limit AS INT)",
            {"batch_id": batch_id, "after": after, "limit": limit},
        )
        return [json.loads(row[0]) for row in rows]

    def item(self, batch_id: str, client_file_id: str) -> dict[str, Any] | None:
        rows = self.sql.execute_sql(
            f"SELECT payload FROM {self.members} WHERE batch_id = :batch_id "
            "AND client_file_id = :client_file_id LIMIT 2",
            {"batch_id": batch_id, "client_file_id": client_file_id},
        )
        if len(rows) > 1:
            raise RuntimeError("Duplicate upload identity requires reconciliation")
        return json.loads(rows[0][0]) if rows else None

    def header_and_item(
        self, batch_id: str, client_file_id: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        rows = self.sql.execute_sql(
            f"SELECT h.payload, i.payload FROM {self.headers} h LEFT JOIN {self.members} i "
            "ON i.batch_id = h.batch_id AND i.client_file_id = :client_file_id "
            "WHERE h.batch_id = :batch_id LIMIT 2",
            {"batch_id": batch_id, "client_file_id": client_file_id},
        )
        if len(rows) > 1:
            raise RuntimeError("Duplicate batch or upload identity requires reconciliation")
        if not rows:
            return None, None
        return json.loads(rows[0][0]), json.loads(rows[0][1]) if rows[0][1] else None

    def counts(self, batch_id: str) -> dict[str, int]:
        rows = self.sql.execute_sql(
            f"SELECT state, COUNT(*) FROM {self.members} WHERE batch_id = :batch_id GROUP BY state",
            {"batch_id": batch_id},
        )
        return {row[0]: int(row[1]) for row in rows}

    def compare_and_set(self, old: dict[str, Any], new: dict[str, Any]) -> bool:
        outcome = RetryOutcome()
        try:
            counts = self.write(
                f"UPDATE {self.members} SET state = :state, "
                "revision = CAST(:next_revision AS INT), payload = :payload "
                "WHERE batch_id = :batch_id AND client_file_id = :client_file_id "
                "AND revision = CAST(:revision AS INT)",
                {
                    "state": new["state"],
                    "next_revision": new["revision"],
                    "payload": json.dumps(new),
                    "batch_id": old["batch_id"],
                    "client_file_id": old["client_file_id"],
                    "revision": old["revision"],
                },
                outcome,
            )
        except Exception as error:
            # The statement may have committed before the error reached us; only a read-back
            # of this transition's own ID can tell. Anything else is the original failure.
            try:
                saved = self.item(old["batch_id"], old["client_file_id"])
            except Exception:
                raise error from None
            if saved is not None and saved["transition_id"] == new["transition_id"]:
                return True
            raise
        affected = counts.get("num_affected_rows")
        # No counts, or a repeat that matched nothing after an attempt that may have committed:
        # only this transition's own ID can tell whether the claim is ours.
        if affected is None or (affected == 0 and outcome.uncertain):
            saved = self.item(old["batch_id"], old["client_file_id"])
            return saved is not None and saved["transition_id"] == new["transition_id"]
        if affected > 1:
            raise RuntimeError("Duplicate upload identity requires reconciliation")
        return affected == 1
