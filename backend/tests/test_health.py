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
            "validation_endpoint": False,
        },
    }
