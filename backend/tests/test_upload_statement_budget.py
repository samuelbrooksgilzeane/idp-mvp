"""Warehouse statement budget for batch uploads, measured against a fake SQL warehouse.

The fake answers only the statements the upload path sends, the way the Statement Execution API
does (DML returns its row counts as a one-row result), so the real Databricks registry and batch
repository code runs unchanged and every statement is counted.
"""

import asyncio
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from databricks.sdk.service import sql
from starlette.datastructures import Headers, UploadFile

from idp_app.services.batch_repository import DatabricksBatchRepository
from idp_app.services.document_registry import (
    DOCUMENT_COLUMNS,
    DatabricksDocumentRegistry,
    DuplicateDocumentError,
)
from idp_app.services.document_storage import LocalVolumeStorage
from idp_app.services.documents import DocumentService, DocumentServiceError
from idp_app.services.upload_batches import UploadBatchService

NAMESPACE = "cat.sch.idp"
DOCUMENTS = f"{NAMESPACE}_documents"
PDF = b"%PDF-1.7\nstatement-budget"


def result(columns: list[str], rows: list[list[Any]]) -> SimpleNamespace:
    return SimpleNamespace(
        statement_id="s",
        status=SimpleNamespace(state=sql.StatementState.SUCCEEDED, error=None),
        manifest=SimpleNamespace(
            schema=SimpleNamespace(columns=[SimpleNamespace(name=name) for name in columns]),
            truncated=False,
        ),
        result=SimpleNamespace(
            data_array=[[None if v is None else str(v) for v in row] for row in rows] or None,
            next_chunk_index=None,
        ),
    )


def dml(affected: int, inserted: int | None = None) -> SimpleNamespace:
    if inserted is None:
        return result(["num_affected_rows"], [[affected]])
    return result(
        ["num_affected_rows", "num_updated_rows", "num_deleted_rows", "num_inserted_rows"],
        [[affected, affected - inserted, 0, inserted]],
    )


class FakeWarehouse:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.documents: dict[str, dict[str, Any]] = {}
        self.statements: list[str] = []
        # statement prefix -> error raised once after the statement applied (answer lost)
        self.lose_answer: dict[str, BaseException] = {}

    def execute_statement(self, *, statement: str, parameters: list[Any], **_: Any) -> Any:
        answer = self.apply(statement, parameters)
        for prefix in list(self.lose_answer):
            if statement.startswith(prefix):
                raise self.lose_answer.pop(prefix)
        return answer

    def apply(self, statement: str, parameters: list[Any]) -> Any:
        self.statements.append(statement)
        p = {item.name: item.value for item in parameters}
        if statement.startswith(f"MERGE INTO {NAMESPACE}_upload_batches"):
            inserted = p["batch_id"] not in self.headers
            self.headers.setdefault(p["batch_id"], p["payload"])
            return dml(int(inserted), int(inserted))
        if statement.startswith(f"MERGE INTO {NAMESPACE}_upload_items"):
            rows = json.loads(p["items"])
            new = [r for r in rows if (r["batch_id"], r["client_file_id"]) not in self.items]
            for row in new:
                self.items[(row["batch_id"], row["client_file_id"])] = row
            return dml(len(new), len(new))
        if statement.startswith(f"SELECT payload FROM {NAMESPACE}_upload_batches"):
            payload = self.headers.get(p["batch_id"])
            return result(["payload"], [[payload]] if payload else [])
        if statement.startswith("SELECT h.payload, i.payload"):
            if p["batch_id"] not in self.headers:
                return result(["payload", "payload"], [])
            item = self.items.get((p["batch_id"], p["client_file_id"]))
            return result(
                ["payload", "payload"],
                [[self.headers[p["batch_id"]], item["payload"] if item else None]],
            )
        if statement.startswith(f"SELECT payload FROM {NAMESPACE}_upload_items"):
            if "client_file_id" in p:
                item = self.items.get((p["batch_id"], p["client_file_id"]))
                return result(["payload"], [[item["payload"]]] if item else [])
            rows = sorted(
                (i for (b, _), i in self.items.items() if b == p["batch_id"]),
                key=lambda i: i["ordinal"],
            )
            rows = [i for i in rows if i["ordinal"] > int(p["after"])][: int(p["limit"])]
            return result(["payload"], [[i["payload"]] for i in rows])
        if statement.startswith("SELECT state, COUNT(*)"):
            counts: dict[str, int] = {}
            for (batch, _), item in self.items.items():
                if batch == p["batch_id"]:
                    counts[item["state"]] = counts.get(item["state"], 0) + 1
            return result(["state", "count"], [[k, v] for k, v in counts.items()])
        if statement.startswith(f"UPDATE {NAMESPACE}_upload_items"):
            item = self.items[(p["batch_id"], p["client_file_id"])]
            if item["revision"] != int(p["revision"]):
                return dml(0)
            item.update(state=p["state"], revision=int(p["next_revision"]), payload=p["payload"])
            return dml(1)
        if statement.startswith(f"MERGE INTO {DOCUMENTS}"):
            existing = self.documents.get(p["content_sha256"])
            if existing and existing["status"] != "DELETED":
                return dml(0, 0)
            row = {name: p.get(name) for name in DOCUMENT_COLUMNS}
            if existing:  # Like the real MERGE, a revived row keeps its document_id.
                row["document_id"] = existing["document_id"]
            self.documents[p["content_sha256"]] = row
            return dml(1, 0 if existing else 1)
        if statement.startswith(f"SELECT {', '.join(DOCUMENT_COLUMNS)} FROM {DOCUMENTS}"):
            if "content_sha256" in p:
                row = self.documents.get(p["content_sha256"])
                matches = [row] if row and row["status"] != "DELETED" else []
            else:
                matches = [
                    r for r in self.documents.values() if r["document_id"] == p["document_id"]
                ]
            return result(
                list(DOCUMENT_COLUMNS), [[r[name] for name in DOCUMENT_COLUMNS] for r in matches]
            )
        raise AssertionError(f"Unexpected statement: {statement}")


@pytest.fixture
def setup(tmp_path: Path):
    warehouse = FakeWarehouse()
    client = SimpleNamespace(statement_execution=warehouse)
    registry = DatabricksDocumentRegistry(client, "wh", "cat", "sch", "idp")  # type: ignore[arg-type]
    documents = DocumentService(LocalVolumeStorage(tmp_path), registry, 1024 * 1024)
    service = UploadBatchService(DatabricksBatchRepository(registry, NAMESPACE), documents)
    return warehouse, service


def pdf_upload(name: str, content: bytes = PDF) -> UploadFile:
    return UploadFile(
        BytesIO(content),
        size=len(content),
        filename=name,
        headers=Headers({"content-type": "application/pdf"}),
    )


def create_batch(service: UploadBatchService, count: int) -> str:
    files = [
        {"client_file_id": f"f{i}", "name": f"f{i}.pdf", "size": len(PDF)} for i in range(count)
    ]
    return str(service.create("user@example.com", "req", None, files)["batch_id"])


def test_new_file_registers_in_at_most_five_statements(setup) -> None:
    warehouse, service = setup
    batch_id = create_batch(service, 1)
    warehouse.statements.clear()
    document = asyncio.run(
        service.upload(batch_id, "f0", "user@example.com", pdf_upload("f0.pdf"))
    )
    assert len(warehouse.statements) <= 5, warehouse.statements
    assert warehouse.documents[document.content_sha256]["document_id"] == document.document_id
    item = json.loads(warehouse.items[(batch_id, "f0")]["payload"])
    assert item["state"] == "REGISTERED"
    assert item["content_sha256"] == document.content_sha256
    assert item["attempts"] == 1


def test_duplicate_content_is_already_registered(setup) -> None:
    warehouse, service = setup
    batch_id = create_batch(service, 2)
    first = asyncio.run(service.upload(batch_id, "f0", "user@example.com", pdf_upload("f0.pdf")))
    warehouse.statements.clear()
    second = asyncio.run(service.upload(batch_id, "f1", "user@example.com", pdf_upload("f1.pdf")))
    assert second.document_id == first.document_id
    assert len(warehouse.documents) == 1
    item = json.loads(warehouse.items[(batch_id, "f1")]["payload"])
    assert item["state"] == "ALREADY_REGISTERED"
    assert item["document_id"] == first.document_id
    assert not any(s.startswith(f"MERGE INTO {DOCUMENTS}") for s in warehouse.statements)


def test_registered_item_replay_reads_without_writing(setup) -> None:
    warehouse, service = setup
    batch_id = create_batch(service, 1)
    asyncio.run(service.upload(batch_id, "f0", "user@example.com", pdf_upload("f0.pdf")))
    warehouse.statements.clear()
    asyncio.run(service.upload(batch_id, "f0", "user@example.com", pdf_upload("f0.pdf")))
    assert not any(s.startswith(("UPDATE", "MERGE")) for s in warehouse.statements)


def test_invalid_body_is_recorded_before_any_registry_work(setup) -> None:
    warehouse, service = setup
    batch_id = create_batch(service, 1)
    warehouse.statements.clear()
    with pytest.raises(Exception, match="PDF signature"):
        asyncio.run(
            service.upload(
                batch_id, "f0", "user@example.com", pdf_upload("f0.pdf", b"x" * len(PDF))
            )
        )
    item = json.loads(warehouse.items[(batch_id, "f0")]["payload"])
    assert item["state"] == "FAILED"
    assert item["error_code"] == "UNSUPPORTED_FILE_TYPE"
    assert item["retryable"] is False
    assert not warehouse.documents
    assert len(warehouse.statements) == 2


def test_revived_deleted_row_is_read_back(setup) -> None:
    warehouse, service = setup
    batch_id = create_batch(service, 1)
    document = asyncio.run(
        service.upload(batch_id, "f0", "user@example.com", pdf_upload("f0.pdf"))
    )
    row = warehouse.documents[document.content_sha256]
    row.update(status="DELETED", document_id="legacy-id")
    registry = service.documents._registry
    warehouse.statements.clear()
    with pytest.raises(DuplicateDocumentError) as raised:
        registry.add(document)
    assert raised.value.document.document_id == "legacy-id"
    assert warehouse.statements[-1].startswith("SELECT")


def item_state(warehouse: FakeWarehouse, batch_id: str) -> dict[str, Any]:
    return json.loads(warehouse.items[(batch_id, "f0")]["payload"])


def test_lost_registry_answer_is_retried_and_read_back(setup) -> None:
    from databricks.sdk.errors import TemporarilyUnavailable

    warehouse, service = setup
    batch_id = create_batch(service, 1)
    warehouse.lose_answer[f"MERGE INTO {DOCUMENTS}"] = TemporarilyUnavailable("lost")
    with patch("idp_app.services.sql_retry.time.sleep"):
        document = asyncio.run(
            service.upload(batch_id, "f0", "user@example.com", pdf_upload("f0.pdf"))
        )
    assert item_state(warehouse, batch_id)["state"] == "REGISTERED"
    assert warehouse.documents[document.content_sha256]["document_id"] == document.document_id


def test_failed_outcome_write_leaves_retryable_failure_not_uploading(setup) -> None:
    warehouse, service = setup
    batch_id = create_batch(service, 1)
    original = service.transition

    def lose_final(item: dict[str, Any], **changes: Any) -> dict[str, Any]:
        if changes.get("state") == "REGISTERED":
            raise RuntimeError("warehouse stopped")
        return original(item, **changes)

    service.transition = lose_final
    with pytest.raises(DocumentServiceError, match="interrupted"):
        asyncio.run(service.upload(batch_id, "f0", "user@example.com", pdf_upload("f0.pdf")))
    item = item_state(warehouse, batch_id)
    assert (item["state"], item["retryable"]) == ("FAILED", True)
    service.transition = original
    asyncio.run(service.upload(batch_id, "f0", "user@example.com", pdf_upload("f0.pdf")))
    assert item_state(warehouse, batch_id)["state"] == "ALREADY_REGISTERED"


def test_cancelled_request_leaves_retryable_failure(setup) -> None:
    warehouse, service = setup
    batch_id = create_batch(service, 1)

    async def disconnected(*_: Any, **__: Any) -> Any:
        raise asyncio.CancelledError

    service.documents.store_and_register = disconnected
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.upload(batch_id, "f0", "user@example.com", pdf_upload("f0.pdf")))
    item = item_state(warehouse, batch_id)
    assert (item["state"], item["retryable"], item["error_code"]) == (
        "FAILED",
        True,
        "UPLOAD_INTERRUPTED",
    )
