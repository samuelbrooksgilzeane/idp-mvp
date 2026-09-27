import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from idp_app.core.config import Settings
from idp_app.main import create_app

ORIGIN = "https://example.cloud.databricks.com"
SPACE = "a" * 32


def test_disabled_config_exposes_no_credentials(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(_env_file=None, local_data_dir=tmp_path)))
    response = client.get("/api/app-config")
    assert response.status_code == 200
    assert response.json()["genie"]["enabled"] is False
    assert response.json()["genie"]["embed_url"] is None
    assert response.headers["content-security-policy"] == "frame-src 'none'"


def test_enabled_config_and_deep_link(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        local_data_dir=tmp_path,
        genie_enabled=True,
        genie_workspace_origin=ORIGIN,
        genie_space_id=SPACE,
        genie_embed_url=f"{ORIGIN}/embed/genie/rooms/{SPACE}",
    )
    client = TestClient(create_app(settings))
    response = client.get("/api/app-config")
    assert response.json()["genie"]["space_id"] == SPACE
    assert response.headers["content-security-policy"] == f"frame-src {ORIGIN}"
    if client.get("/").status_code == 200:
        assert client.get("/ask-genie").text == client.get("/").text


@pytest.mark.parametrize(
    "change",
    [
        {"genie_workspace_origin": "http://example.com"},
        {"genie_workspace_origin": ORIGIN + "/path"},
        {"genie_space_id": "-" * 36},
        {"genie_embed_url": f"https://evil.example/embed/genie/rooms/{SPACE}"},
        {"genie_embed_url": f"{ORIGIN}/embed/genie/rooms/{'b' * 32}"},
        {"genie_embed_url": f"{ORIGIN}/embed/genie/rooms/{SPACE}?redirect=evil"},
    ],
)
def test_rejects_untrusted_embed_configuration(change: dict[str, str]) -> None:
    values = {"genie_workspace_origin": ORIGIN, "genie_space_id": SPACE, **change}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, genie_enabled=True, **values)


def provisioning_module():
    path = Path(__file__).resolve().parents[2] / "scripts/provision_genie.py"
    spec = importlib.util.spec_from_file_location("provision_genie", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_provision_reuses_space_and_requires_review_for_changes(tmp_path: Path) -> None:
    module = provisioning_module()
    definition = module.render("workspace", "idp", "dev")
    client = SimpleNamespace(config=SimpleNamespace(host=ORIGIN), genie=Mock())
    client.genie.create_space.return_value = SimpleNamespace(space_id=SPACE)
    state = tmp_path / "state.json"
    assert module.reconcile(client, state, definition, "warehouse", "project") == SPACE
    client.genie.get_space.return_value = SimpleNamespace(serialized_space=definition, etag="1")
    module.reconcile(client, state, definition, "warehouse", "project")
    client.genie.create_space.assert_called_once()
    client.genie.update_space.assert_not_called()
    remote = json.dumps({"version": 2, "human_edit": True})
    client.genie.get_space.return_value = SimpleNamespace(serialized_space=remote, etag="2")
    assert module.reconcile(client, state, definition, "warehouse", "project") == SPACE
    assert json.loads(state.with_suffix(".remote.json").read_text())["human_edit"] is True
    with pytest.raises(ValueError, match="Review"):
        module.reconcile(client, state, definition, "warehouse", "project", "stale-hash")
    client.genie.update_space.assert_not_called()
    module.reconcile(client, state, definition, "warehouse", "project", module.digest(remote))
    assert client.genie.update_space.call_args.kwargs["etag"] == "2"


def test_uncertain_create_never_automatically_retries(tmp_path: Path) -> None:
    module = provisioning_module()
    client = SimpleNamespace(config=SimpleNamespace(host=ORIGIN), genie=Mock())
    client.genie.create_space.side_effect = TimeoutError()
    state = tmp_path / "state.json"
    with pytest.raises(TimeoutError):
        module.reconcile(client, state, "{}", "warehouse", "project")
    with pytest.raises(ValueError, match="Uncertain"):
        module.reconcile(client, state, "{}", "warehouse", "project")
    client.genie.create_space.assert_called_once()


def test_project_template_starts_without_structured_sources() -> None:
    definition = json.loads(provisioning_module().render("workspace", "idp", "dev"))
    assert definition["data_sources"] == {"tables": []}
    assert "example_question_sqls" not in definition["instructions"]
    assert "volumes" not in definition["data_sources"]  # Never guess preview export fields.
