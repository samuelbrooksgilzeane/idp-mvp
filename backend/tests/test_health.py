from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from idp_app.core.config import Settings
from idp_app.main import create_app


def test_health_returns_safe_mock_configuration_state() -> None:
    client = TestClient(create_app(Settings(_env_file=None, app_name="Test IDP")))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "mode": "mock",
        "application_name": "Test IDP",
        "configuration": {
            "catalog": False,
            "project_schema": False,
            "table_prefix": False,
            "source_volume_name": False,
            "artifacts_volume_name": False,
            "warehouse_id": False,
                "parse_job_id": False,
                "extraction_job_id": False,
            "validation_endpoint": False,
        },
    }


def test_client_routes_fall_back_to_the_application_entry_point(tmp_path: Path) -> None:
    """A deep link or refresh on a client route must serve the app, not a 404."""
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        pytest.skip("frontend production build is not present")

    client = TestClient(create_app(Settings(_env_file=None, local_data_dir=tmp_path / "idp")))

    root = client.get("/")
    deep_link = client.get("/documents/9e4ef80e-fef3-5e13-ae29-f8dc585b15cb")
    assert deep_link.status_code == 200
    assert deep_link.text == root.text

    # A missing asset must still 404 rather than silently returning HTML.
    assert client.get("/assets/does-not-exist.js").status_code == 404
    # API routes keep their own error contract.
    assert client.get("/api/documents/not-a-document").status_code in {404, 422}
