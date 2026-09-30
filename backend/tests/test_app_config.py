from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from idp_app.core.config import Settings
from idp_app.main import create_app

CHAT_URL = "https://idp-chat-123.aws.databricksapps.com"


def test_chat_link_is_hidden_by_default_and_frames_are_blocked(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(_env_file=None, local_data_dir=tmp_path)))
    response = client.get("/api/app-config")
    assert response.status_code == 200
    assert response.json()["chat_app_url"] is None
    assert response.headers["content-security-policy"] == "frame-src 'none'"


def test_configured_chat_link_is_exposed(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, local_data_dir=tmp_path, chat_app_url=f" {CHAT_URL} ")
    response = TestClient(create_app(settings)).get("/api/app-config")
    assert response.json()["chat_app_url"] == CHAT_URL


def test_blank_chat_link_counts_as_unset() -> None:
    assert Settings(_env_file=None, chat_app_url="  ").chat_app_url is None


@pytest.mark.parametrize(
    "url",
    ["http://example.com", "javascript:alert(1)", "https://user:pw@example.com", "https://"],
)
def test_rejects_unsafe_chat_link(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, chat_app_url=url)
