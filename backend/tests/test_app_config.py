from pathlib import Path

from fastapi.testclient import TestClient

from idp_app.core.config import Settings
from idp_app.main import create_app


def test_app_config_reports_only_display_values_and_frames_are_blocked(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(_env_file=None, local_data_dir=tmp_path)))
    response = client.get("/api/app-config")
    assert response.status_code == 200
    assert set(response.json()) == {"project_name"}
    assert response.headers["content-security-policy"] == "frame-src 'none'"
