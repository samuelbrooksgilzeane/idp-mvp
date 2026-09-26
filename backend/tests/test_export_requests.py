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
