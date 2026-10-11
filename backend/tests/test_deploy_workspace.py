"""The one-command workspace deploy, against a fake Databricks CLI; no workspace."""

import importlib.util
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script():
    spec = importlib.util.spec_from_file_location(
        "deploy_workspace", ROOT / "scripts/deploy_workspace.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


deploy = load_script()


def validated(**overrides: Any) -> dict[str, Any]:
    values = {
        "catalog": "main",
        "project_schema": "idp_mvp",
        "table_prefix": "idp",
        "source_volume_name": "idp_source",
        "artifacts_volume_name": "idp_artifacts",
        "import_volume_name": "idp_import",
        "warehouse_id": "abc123",
        "chat_mode": "knowledge_assistant",
        **overrides,
    }
    return {
        "bundle": {"target": "us", "mode": "production"},
        "workspace": {"host": "https://us.cloud.databricks.com"},
        "variables": {name: {"value": value} for name, value in values.items()},
        "resources": {"apps": {"idp_app": {"name": "idp-mvp-us"}}},
    }


class FakeCli:
    def __init__(self, *, app_exists: bool, fail: Sequence[str] = ()) -> None:
        self.app_exists = app_exists
        self.fail = list(fail)
        self.commands: list[str] = []
        self.config = validated()

    def __call__(self, command: Sequence[str], capture: bool) -> Any:
        line = " ".join(command[1:])
        self.commands.append(line)
        if any(line.startswith(prefix) for prefix in self.fail):
            self.fail.remove(next(p for p in self.fail if line.startswith(p)))
            return deploy.Result(1, "", "boom")
        if line == "--version":
            return deploy.Result(0, "Databricks CLI v0.260.0\n")
        if line.startswith("bundle validate"):
            return deploy.Result(0, json.dumps(self.config))
        if line.startswith("apps get"):
            if not self.app_exists:
                return deploy.Result(1, "", "does not exist")
            return deploy.Result(0, json.dumps({"url": "https://idp-mvp-us.example.com"}))
        if line == "bundle deploy -t us" and not self.app_exists:
            self.app_exists = True  # the second deploy creates the App
            return deploy.Result(1, "", "app grants name missing tables")
        return deploy.Result(0)


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    targets = tmp_path / "targets"
    targets.mkdir()
    (targets / "us.yml").write_text("targets:\n  us:\n    workspace:\n      host: https://us\n")
    monkeypatch.setattr(deploy, "TARGETS_DIR", targets)
    return targets / "us.yml"


def deployer(cli: FakeCli, lines: list[str]) -> Any:
    return deploy.Deployer(
        "us", profile=None, executable="databricks", runner=cli, say=lines.append
    )


def test_first_deployment_tolerates_the_app_error_then_grants_the_new_app(settings_file) -> None:
    cli, lines = FakeCli(app_exists=False), []

    url = deployer(cli, lines).deploy()

    assert url == "https://idp-mvp-us.example.com"
    assert [c for c in cli.commands if c.startswith("bundle")] == [
        "bundle validate -o json -t us",
        "bundle deploy -t us",
        "bundle run workspace_setup -t us",
        "bundle deploy -t us",
        "bundle run app_access_grants -t us",
        "bundle run idp_app -t us",
    ]
    assert any("As expected on a first deployment" in line for line in lines)


def test_later_deployment_stops_on_a_deploy_error(settings_file) -> None:
    cli, lines = FakeCli(app_exists=True, fail=["bundle deploy"]), []

    with pytest.raises(deploy.DeployError, match="The deploy failed"):
        deployer(cli, lines).deploy()
    assert "bundle run workspace_setup -t us" not in cli.commands


def test_later_deployment_skips_the_extra_grants(settings_file) -> None:
    cli = FakeCli(app_exists=True)
    deployer(cli, []).deploy()
    assert "bundle run app_access_grants -t us" not in cli.commands


def test_failed_setup_names_the_next_step(settings_file) -> None:
    cli = FakeCli(app_exists=True, fail=["bundle run workspace_setup"])
    with pytest.raises(deploy.DeployError, match="Agent Bricks"):
        deployer(cli, []).deploy()


def test_profile_is_passed_to_every_workspace_call(settings_file) -> None:
    cli = FakeCli(app_exists=True)
    deploy.Deployer("us", profile="us", executable="databricks", runner=cli, say=print).deploy()
    assert all(c.endswith("-p us") for c in cli.commands if c != "--version")


def test_missing_settings_file_points_at_the_template(settings_file) -> None:
    settings_file.unlink()
    with pytest.raises(deploy.DeployError, match="example.yml.template"):
        deployer(FakeCli(app_exists=True), []).deploy()


@pytest.mark.parametrize("target", ["dev", "prod", "US", "a" * 21])
def test_reserved_or_invalid_target_names_are_refused(target: str) -> None:
    with pytest.raises(deploy.DeployError, match="target name"):
        deploy.Deployer(target, profile=None, executable="x", runner=FakeCli(app_exists=True),
                        say=print).check_files()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"chat_mode": "false"}, "chat_mode"),
        ({"warehouse_id": "YOUR_WAREHOUSE_ID"}, "warehouse_id"),
        ({"catalog": "YOUR_CATALOG"}, "catalog"),
        ({"table_prefix": "idp-prod"}, "table_prefix"),
    ],
)
def test_settings_the_app_cannot_use_are_explained(overrides, message: str) -> None:
    with pytest.raises(deploy.DeployError, match=message):
        deploy.check_config(validated(**overrides), "us")


def test_development_mode_and_long_app_names_are_refused() -> None:
    config = validated()
    config["bundle"]["mode"] = "development"
    with pytest.raises(deploy.DeployError, match="mode: production"):
        deploy.check_config(config, "us")
    config = validated()
    config["resources"]["apps"]["idp_app"]["name"] = "idp-mvp-" + "x" * 30
    with pytest.raises(deploy.DeployError, match="30 characters"):
        deploy.check_config(config, "us")


def test_cluster_id_is_removed_from_the_cli_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(command, **kwargs):
        seen.update(kwargs)
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setenv("DATABRICKS_CLUSTER_ID", "0101-abc")
    monkeypatch.setattr(deploy.subprocess, "run", fake_run)
    deploy.run_command(["databricks", "--version"], True)
    assert "DATABRICKS_CLUSTER_ID" not in seen["env"]
    assert seen["cwd"] == ROOT / "databricks_etl"


def test_adopting_binds_the_existing_app_before_deploying(settings_file) -> None:
    cli, lines = FakeCli(app_exists=True), []
    deploy.Deployer(
        "us", profile=None, executable="databricks", runner=cli, say=lines.append, adopt_app=True
    ).deploy()

    bundle_commands = [c for c in cli.commands if c.startswith("bundle")]
    assert bundle_commands[1:3] == [
        "bundle deployment bind idp_app idp-mvp-us --auto-approve -t us",
        "bundle deploy -t us",
    ]
    assert "bundle run app_access_grants -t us" not in bundle_commands


def test_adopting_tolerates_an_app_already_bound(settings_file) -> None:
    cli, lines = FakeCli(app_exists=True, fail=["bundle deployment bind"]), []
    deploy.Deployer(
        "us", profile=None, executable="databricks", runner=cli, say=lines.append, adopt_app=True
    ).deploy()
    assert any("already manages the App" in line for line in lines)


def test_adopting_a_missing_app_stops_before_deploying(settings_file) -> None:
    cli = FakeCli(app_exists=False)
    with pytest.raises(deploy.DeployError, match="no App named idp-mvp-us"):
        deploy.Deployer(
            "us", profile=None, executable="databricks", runner=cli, say=print, adopt_app=True
        ).deploy()
    assert "bundle deploy -t us" not in cli.commands
