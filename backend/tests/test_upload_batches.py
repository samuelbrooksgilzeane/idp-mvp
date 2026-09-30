from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from idp_app.core.config import Settings
from idp_app.main import create_app
from idp_app.services.batch_repository import DatabricksBatchRepository, SQLiteBatchRepository

PDF = b"%PDF-1.7\nsmall-local-test"


@pytest.fixture
def client(tmp_path: Path):
    with TestClient(create_app(Settings(_env_file=None, local_data_dir=tmp_path))) as client:
        yield client


def create(client: TestClient, *, key: str = "request-1", count: int = 1):
    return client.post(
        "/api/upload-batches",
        json={
            "client_request_id": key,
            "case_id": "case-a",
            "files": [
                {"client_file_id": f"file-{i}", "name": f"original {i}.pdf", "size": len(PDF)}
                for i in range(count)
            ],
        },
    )


def upload(client: TestClient, batch_id: str, index: int = 0, content: bytes = PDF):
    return client.post(
        "/api/documents",
        data={"upload_batch_id": batch_id, "client_file_id": f"file-{index}"},
        files={"files": (f"original {index}.pdf", content, "application/pdf")},
    )


def test_manifest_replay_and_full_pagination_without_uploading(client: TestClient):
    response = create(client, count=1000)
    assert response.status_code == 201
    batch = response.json()
    assert batch["counts"] == {"QUEUED": 1000}
    assert create(client, count=1000).json()["batch_id"] == batch["batch_id"]
    assert create(client, count=1).status_code == 409
    assert create(client, count=1001).status_code == 422
    rows = []
    cursor = "-1"
    while cursor is not None:
        page = client.get(
            f"/api/upload-batches/{batch['batch_id']}/items", params={"cursor": cursor}
        ).json()
        assert len(page["items"]) <= 50
        rows.extend(page["items"])
        cursor = page["next_cursor"]
    assert len({row["client_file_id"] for row in rows}) == 1000
    assert "lease_owner" not in rows[0]
    assert "content_sha256" not in rows[0]


def test_registration_replay_and_duplicate_outcome(client: TestClient):
    batch = create(client, count=2).json()
    first = upload(client, batch["batch_id"])
    assert first.status_code == 201
    assert upload(client, batch["batch_id"]).json() == first.json()
    assert upload(client, batch["batch_id"], index=1).status_code == 201
    page = client.get(f"/api/upload-batches/{batch['batch_id']}/items").json()
    assert [row["state"] for row in page["items"]] == ["REGISTERED", "ALREADY_REGISTERED"]
    assert page["items"][0]["document_id"] == page["items"][1]["document_id"]
    assert page["items"][0]["name"] == "original 0.pdf"
    assert page["items"][0]["attempts"] == 1


def test_authorization_precedes_storage_and_manifest_paths_are_rejected(client: TestClient):
    batch = create(client).json()
    response = client.post(
        "/api/documents",
        headers={"x-forwarded-email": "other@example.com"},
        data={"upload_batch_id": batch["batch_id"], "client_file_id": "file-0"},
        files={"files": ("original 0.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 404
    assert (
        client.get(
            f"/api/upload-batches/{batch['batch_id']}",
            headers={"x-forwarded-email": "other@example.com"},
        ).status_code
        == 404
    )
    assert client.get("/api/documents").json() == []
    assert (
        client.post(
            "/api/upload-batches",
            json={
                "client_request_id": "bad",
                "files": [{"client_file_id": "a", "name": "/private/local.pdf", "size": 1}],
            },
        ).status_code
        == 422
    )


def test_failed_item_records_error_and_rejects_different_content(client: TestClient):
    batch = create(client).json()
    service = client.app.state.document_service
    original = service._storage.store
    service._storage.store = MagicMock(side_effect=OSError("offline"))
    assert upload(client, batch["batch_id"]).status_code == 502
    item = client.get(f"/api/upload-batches/{batch['batch_id']}/items").json()["items"][0]
    assert item["state"] == "FAILED"
    assert item["retryable"] is True
    assert item["error_code"] == "FILE_STORAGE_FAILED"
    service._storage.store = original
    changed = PDF[:-1] + b"X"
    assert (
        upload(client, batch["batch_id"], content=changed).json()["error"]["code"]
        == "FILE_CONTENT_MISMATCH"
    )
    assert upload(client, batch["batch_id"]).status_code == 201


def test_registry_failure_recovers_verified_orphan_without_overwrite(client: TestClient):
    batch = create(client).json()
    service = client.app.state.document_service
    original = service._registry.add
    service._registry.add = MagicMock(side_effect=OSError("offline"))
    assert upload(client, batch["batch_id"]).json()["error"]["code"] == "REGISTRY_WRITE_FAILED"
    service._registry.add = original
    assert upload(client, batch["batch_id"]).status_code == 201
    assert len(client.get("/api/documents").json()) == 1


def test_claim_compare_and_set_has_one_winner_after_restart(client: TestClient, tmp_path: Path):
    batch = create(client).json()
    repository = SQLiteBatchRepository(tmp_path / "registry.sqlite3")
    old = repository.item(batch["batch_id"], "file-0")
    barrier = Barrier(2)

    def claim(owner: str):
        replica = SQLiteBatchRepository(tmp_path / "registry.sqlite3")
        barrier.wait()
        return replica.compare_and_set(
            old,
            {
                **old,
                "state": "UPLOADING",
                "revision": 1,
                "lease_owner": owner,
                "transition_id": owner,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["a", "b"]))
    assert sorted(results) == [False, True]


def test_databricks_creation_bulk_writes_and_claim_uses_revision():
    sql = MagicMock()
    sql.execute_dml.return_value = {"num_affected_rows": 1}
    repository = DatabricksBatchRepository(sql, "catalog.project.idp")
    repository.header = MagicMock(return_value={"manifest_hash": "hash"})
    repository.create({"batch_id": "b", "manifest_hash": "hash"}, [])
    assert sql.execute_dml.call_count == 2
    assert "explode(from_json(:items" in sql.execute_dml.call_args.args[0]
    repository.item = MagicMock(side_effect=AssertionError("counts decide the claim"))
    old = {"batch_id": "b", "client_file_id": "i", "revision": 0}
    new = {"state": "UPLOADING", "revision": 1, "transition_id": "winner"}
    assert repository.compare_and_set(old, new)
    assert "AND revision = CAST(:revision AS INT)" in sql.execute_dml.call_args.args[0]
    sql.execute_dml.return_value = {"num_affected_rows": 0}
    assert not repository.compare_and_set(old, new)


def test_databricks_claim_reads_back_only_when_outcome_is_unknown():
    sql = MagicMock()
    repository = DatabricksBatchRepository(sql, "catalog.project.idp")
    old = {"batch_id": "b", "client_file_id": "i", "revision": 0}
    new = {"state": "UPLOADING", "revision": 1, "transition_id": "winner"}
    sql.execute_dml.side_effect = TimeoutError("connection lost after submit")
    repository.item = MagicMock(return_value={"transition_id": "winner"})
    assert repository.compare_and_set(old, new)
    repository.item = MagicMock(return_value={"transition_id": "someone-else"})
    with pytest.raises(TimeoutError):
        repository.compare_and_set(old, new)
    sql.execute_dml.side_effect = None
    sql.execute_dml.return_value = {}  # No counts reported: confirm by transition ID.
    repository.item = MagicMock(return_value={"transition_id": "winner"})
    assert repository.compare_and_set(old, new)
    repository.item.assert_called_once()


def test_delta_conflict_retries_are_logged(caplog: pytest.LogCaptureFixture):
    sql = MagicMock()
    sql.execute_dml.side_effect = [RuntimeError("[DELTA_CONCURRENT_APPEND] conflict"), {}]
    repository = DatabricksBatchRepository(sql, "catalog.project.idp")
    with caplog.at_level("WARNING"), patch("idp_app.services.batch_repository.time.sleep"):
        repository.write("UPDATE t SET x = 1", {})
    [record] = caplog.records
    assert record.error_code == "DELTA_CONCURRENT"  # type: ignore[attr-defined]
    assert record.attempt == 1  # type: ignore[attr-defined]


def test_gateway_failure_is_durable_but_cannot_overwrite_success(client: TestClient):
    batch = create(client).json()
    url = f"/api/upload-batches/{batch['batch_id']}/items/file-0"
    response = client.post(url + "/transport-failure", json={"code": "HTTP_413"})
    assert response.status_code == 200
    assert client.get(url).json()["error_code"] == "HTTP_413"
    assert client.get(url).json()["retryable"] is False
    assert upload(client, batch["batch_id"]).status_code == 201
    response = client.post(url + "/transport-failure", json={"code": "HTTP_503"})
    assert response.json()["state"] == "REGISTERED"


def test_committed_registration_repairs_missing_item_outcome_without_reupload(client: TestClient):
    batch = create(client).json()
    assert upload(client, batch["batch_id"]).status_code == 201
    repository = client.app.state.upload_batch_service.repository
    item = repository.item(batch["batch_id"], "file-0")
    item["state"] = "UPLOADING"
    item["lease_expires_at"] = "2099-01-01T00:00:00+00:00"
    assert repository.compare_and_set(item, {**item, "revision": item["revision"] + 1})
    client.app.state.document_service._storage.store = MagicMock(
        side_effect=AssertionError("must reuse registry")
    )
    assert upload(client, batch["batch_id"]).status_code == 201
    assert repository.item(batch["batch_id"], "file-0")["state"] == "ALREADY_REGISTERED"


def test_source_reconciliation_reports_ambiguity_without_deleting(
    client: TestClient, tmp_path: Path
):
    batch = create(client).json()
    document = upload(client, batch["batch_id"]).json()["documents"][0]
    source_dir = tmp_path / "source_volume" / "incoming"
    (source_dir / f"{document['document_id']}.pdf").unlink()
    orphan = source_dir / "orphan.pdf"
    orphan.write_bytes(PDF)
    findings = client.app.state.document_service.reconcile_sources()
    assert {row["kind"] for row in findings} == {"MISSING_SOURCE", "UNREGISTERED_SOURCE"}
    assert orphan.exists()
    assert client.get(f"/api/documents/{document['document_id']}").status_code == 200


def test_upload_limits_follow_deployment_configuration(tmp_path: Path):
    with TestClient(
        create_app(
            Settings(
                _env_file=None,
                local_data_dir=tmp_path,
                max_upload_batch_files=12,
                max_upload_bytes=1024,
                upload_parallel_transfers=6,
            )
        )
    ) as client:
        limits = client.get("/api/upload-batches/limits").json()
        assert len(limits.pop("cache_scope")) == 64
        assert limits == {
            "max_files": 12,
            "max_file_bytes": 1024,
            "parallel_transfers": 6,
            "automatic_preparation": False,
            "bulk_extraction": False,
            "bulk_export": False,
        }
        assert create(client, count=13).status_code == 422
