from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from test_generic_exports import _nested_schema, _run

from idp_app.services.documents import DocumentServiceError
from idp_app.services.export_artifacts import ExportArtifacts
from idp_app.services.export_jobs import run_export
from idp_app.services.export_requests import ExportRequests
from idp_app.services.export_service import ExportService
from idp_app.services.export_sources import ExportSource


def test_replay_owner_and_restart(tmp_path: Path):
    requests = ExportRequests(tmp_path / "db")
    token = str(uuid4())
    row = requests.create("a", token, "xlsx", ["run", "run"])
    assert requests.create("a", token, "xlsx", ["run"]) == row
    with pytest.raises(DocumentServiceError):
        requests.create("a", token, "csv", ["run"])
    with pytest.raises(DocumentServiceError):
        requests.owned(row["export_id"], "b")
    assert ExportRequests(tmp_path / "db").owned(row["export_id"], "a")["run_ids"] == ["run"]
    row["expires_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    requests.save(row)
    assert requests.owned(row["export_id"], "a")["state"] == "EXPIRED"


def test_worker_restarts_from_pins_and_paged_sources(tmp_path: Path):
    requests = ExportRequests(tmp_path / "db")
    ids = [str(uuid4()) for _ in range(1000)]
    row = requests.create("a", str(uuid4()), "csv", ids)
    row["state"] = "RUNNING"
    requests.save(row)

    class Sources:
        calls = 0

        def get_many(self, selected):
            self.calls += 1
            assert len(selected) <= 25
            return [
                ExportSource(
                    replace(
                        _run(),
                        extraction_run_id=i,
                        ai_result={"response": {"invoices": []}, "metadata": {"citations": []}},
                    ),
                    _nested_schema(),
                    "test.pdf",
                )
                for i in selected
            ]

    sources = Sources()
    artifacts = ExportArtifacts(str(tmp_path / "artifacts"))
    run_export(ExportRequests(tmp_path / "db"), ExportService(sources), artifacts, row["export_id"])
    complete = requests.owned(row["export_id"], "a")
    assert complete["state"] == "SUCCEEDED"
    assert complete["runs_processed"] == 1000
    assert sources.calls == 40
    with artifacts.open(row["export_id"]) as stream:
        assert len(stream.read()) == complete["bytes"]
    run_export(requests, ExportService(sources), artifacts, row["export_id"])
    assert sources.calls == 40


def test_artifacts_reject_untrusted_paths(tmp_path: Path):
    with pytest.raises(ValueError):
        ExportArtifacts(str(tmp_path)).open("../../secrets")


def test_api_confirms_cross_page_duplicates_and_replays_without_reresolving(tmp_path: Path):
    from fastapi.testclient import TestClient

    from idp_app.core.config import Settings
    from idp_app.main import create_app

    app = create_app(Settings(local_data_dir=tmp_path, bulk_export_enabled=True))
    ids = [str(uuid4()), str(uuid4())]

    class Sources:
        calls = 0

        def get_many(self, selected):
            self.calls += 1
            return [
                ExportSource(
                    replace(_run(), extraction_run_id=i, ai_result={"response": {}}),
                    _nested_schema(),
                    "test.pdf",
                )
                for i in selected
            ]

    sources = Sources()
    requests = ExportRequests(tmp_path / "exports.sqlite")
    app.state.durable_exports = (requests, ExportService(sources), ExportArtifacts(str(tmp_path)))
    client = TestClient(app)
    body = {"client_request_id": str(uuid4()), "format": "xlsx", "run_ids": ids}
    confirmation = client.post("/api/export-requests", json=body)
    assert confirmation.status_code == 409
    assert confirmation.json()["code"] == "CONFIRM_HISTORICAL_DUPLICATES"
    body["include_historical_duplicates"] = True
    accepted = client.post("/api/export-requests", json=body)
    assert accepted.status_code == 202
    before = sources.calls
    assert client.post("/api/export-requests", json=body).json() == accepted.json()
    assert sources.calls == before
    identity = accepted.json()["export_id"]
    assert (
        client.get(
            f"/api/export-requests/{identity}", headers={"x-forwarded-email": "other"}
        ).status_code
        == 404
    )
    assert client.get(
        f"/api/export-requests/{identity}/download", headers={"x-forwarded-email": "other"}
    ).status_code == 404
    assert client.get(f"/api/export-requests/{identity}/download").status_code == 409
    body["format"] = "csv"
    assert client.post("/api/export-requests", json=body).status_code == 409


def test_artifact_missing_from_databricks_storage_is_a_410_not_a_500(tmp_path: Path):
    from types import SimpleNamespace

    from databricks.sdk.errors import NotFound
    from fastapi.testclient import TestClient

    from idp_app.core.config import Settings
    from idp_app.main import create_app

    def download(path: str):
        raise NotFound(f"{path} does not exist")

    client = SimpleNamespace(files=SimpleNamespace(download=download))
    volume = ExportArtifacts("/Volumes/c/s/artifacts", client)
    with pytest.raises(FileNotFoundError):
        volume.open(str(uuid4()))

    app = create_app(Settings(local_data_dir=tmp_path, bulk_export_enabled=True))
    requests = ExportRequests(tmp_path / "exports.sqlite")
    row = requests.create("local-development-user", str(uuid4()), "csv", [str(uuid4())])
    row.update(state="SUCCEEDED", filename="results.csv", bytes=10, media_type="text/csv")
    requests.save(row)
    app.state.durable_exports = (requests, None, volume)
    missing = TestClient(app).get(f"/api/export-requests/{row['export_id']}/download")
    assert missing.status_code == 410
    assert missing.json()["error"]["code"] == "EXPORT_ARTIFACT_MISSING"
