"""Deploy the IDP app to one workspace from its settings file, with one command.

    python3 scripts/deploy_workspace.py <target> [--profile NAME] [--adopt-app]
    (Windows: py scripts\\deploy_workspace.py <target>)

<target> names databricks_etl/targets/<target>.yml (a copy of targets/example.yml.template). Needs
only Python 3.10 or later and the Databricks CLI, signed in to that workspace: no uv, Node or make.
It deploys, runs the workspace_setup Job (tables, document chat, access), deploys again, starts the
App and prints its address. Every step is safe to repeat, so after fixing a failure run it again.

--adopt-app, once: an App with the settings' deployment_name already exists, deployed some other
way (by hand, or from another target); this deployment takes it over, so it keeps its address.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_DIR = ROOT / "databricks_etl"
TARGETS_DIR = BUNDLE_DIR / "targets"
TARGET_NAME = re.compile(r"^[a-z][a-z0-9-]{0,19}$")
SIMPLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
APP_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,28}[a-z0-9]$")
CHAT_MODES = ("off", "knowledge_assistant", "supervisor")
IDENTIFIER_VARIABLES = (
    "catalog",
    "project_schema",
    "table_prefix",
    "source_volume_name",
    "artifacts_volume_name",
    "import_volume_name",
)
TESTED_CLI = (0, 250, 0)


class DeployError(Exception):
    """A step failed; the message says what to do next."""


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


# (command, capture output) -> Result. Uncaptured output streams to the terminal.
Runner = Callable[[Sequence[str], bool], Result]


def run_command(command: Sequence[str], capture: bool) -> Result:
    # In a cluster's web terminal DATABRICKS_CLUSTER_ID is set, and the CLI would move every Job
    # onto that cluster; this deployment runs its Jobs on serverless compute.
    env = {key: value for key, value in os.environ.items() if key != "DATABRICKS_CLUSTER_ID"}
    done = subprocess.run(
        list(command), cwd=BUNDLE_DIR, env=env, capture_output=capture, text=True, check=False
    )
    return Result(done.returncode, done.stdout or "", done.stderr or "")


class Deployer:
    def __init__(
        self,
        target: str,
        *,
        profile: str | None,
        executable: str,
        adopt_app: bool = False,
        runner: Runner = run_command,
        say: Callable[[str], None] = print,
    ) -> None:
        self.target = target
        self.profile = profile
        self.executable = executable
        self.adopt_app = adopt_app
        self.runner = runner
        self.say = say
        self.steps = 0

    # Steps -------------------------------------------------------------------------------------

    def deploy(self) -> str:
        """Run every step; returns the App's address."""
        self.check_cli()
        self.check_files()
        config = self.validate()
        app_name = config["resources"]["apps"]["idp_app"]["name"]
        first = not self.app_exists(app_name)
        if self.adopt_app:
            self.adopt(app_name, exists=not first)
        self.step(
            "Deploying the Jobs and the App"
            + (" (first deployment of this target)" if first else "")
        )
        if self.bundle("deploy").returncode != 0:
            if not first:
                raise DeployError(
                    "The deploy failed (details above). Fix the cause, often a value in "
                    f"targets/{self.target}.yml or a missing permission to create Jobs or Apps, "
                    "then run this command again. If it says an App with this name already "
                    "exists, it was deployed some other way: run again with --adopt-app to keep "
                    "it and its address."
                )
            self.say(
                "  As expected on a first deployment, the App could not be created yet: its access "
                "names tables that workspace setup creates next. Continuing."
            )
        self.step(
            "Running workspace setup: tables and migrations, document chat, access grants. "
            "This takes several minutes; the run link appears below."
        )
        if self.bundle("run", "workspace_setup").returncode != 0:
            raise DeployError(
                "Workspace setup failed. Open the run link above and the failed task's output "
                "says why. If provision_chat failed because Agent Bricks is off, a workspace admin "
                'can turn it on, or set chat_mode: "off" in '
                f"targets/{self.target}.yml. Then run this command again."
                + (" The first deploy also reported an error above." if first else "")
            )
        self.step("Deploying again, now that the tables exist")
        if self.bundle("deploy").returncode != 0:
            raise DeployError(
                "The second deploy failed (details above). Run this command again; if it fails "
                "the same way, the error names the setting or permission to fix."
            )
        if first:
            self.step("Granting the new App its remaining access")
            if self.bundle("run", "app_access_grants").returncode != 0:
                raise DeployError(
                    "Granting the App access failed (run link above). Run this command again."
                )
        self.step("Starting the App")
        if self.bundle("run", "idp_app").returncode != 0:
            raise DeployError(
                f"The App did not start. Open Compute > Apps > {app_name} > Logs to see why, then "
                "run this command again."
            )
        return self.app_url(app_name)

    def adopt(self, app_name: str, *, exists: bool) -> None:
        self.step(f"Taking over the existing App {app_name}, so it keeps its address")
        if not exists:
            raise DeployError(
                f"There is no App named {app_name} in this workspace to take over. Check "
                f"deployment_name in targets/{self.target}.yml, or run without --adopt-app."
            )
        if self.bundle("deployment", "bind", "idp_app", app_name, "--auto-approve").returncode:
            self.say(
                "  The CLI did not take it over (details above). If it says this deployment "
                "already manages the App, that is fine. Continuing."
            )

    def check_cli(self) -> None:
        self.step("Checking the Databricks CLI")
        result = self.cli("--version", capture=True, profile=False)
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout)
        if result.returncode != 0 or match is None:
            raise DeployError("The Databricks CLI did not run. Install it, then try again.")
        version = tuple(int(part) for part in match.groups())
        self.say(f"  Databricks CLI {'.'.join(map(str, version))}")
        if version < TESTED_CLI:
            self.say(
                "  This CLI is older than the versions this was tested with "
                f"({'.'.join(map(str, TESTED_CLI))}). If a step fails, update the CLI first."
            )

    def check_files(self) -> None:
        self.step(f"Checking targets/{self.target}.yml")
        if self.target in ("dev", "prod") or TARGET_NAME.fullmatch(self.target) is None:
            raise DeployError(
                "The target name must be lowercase letters, digits and hyphens (up to 20, "
                "starting with a letter), and not dev or prod, which databricks.yml defines."
            )
        settings = TARGETS_DIR / f"{self.target}.yml"
        if not settings.is_file():
            raise DeployError(
                f"There is no settings file databricks_etl/targets/{self.target}.yml. Copy "
                f"databricks_etl/targets/example.yml.template to that name, fill it in, and "
                "run this command again."
            )
        if not (ROOT / "frontend" / "dist" / "index.html").is_file():
            raise DeployError(
                "frontend/dist is missing. It is committed to the repository; update your copy "
                "of the code (git pull, or pull in the workspace Git folder) and try again."
            )

    def validate(self) -> dict[str, Any]:
        self.step("Validating the settings with the Databricks CLI")
        result = self.bundle("validate", "-o", "json", capture=True)
        if result.returncode != 0:
            self.say(result.stderr.strip())
            host = self.settings_host()
            raise DeployError(
                f"The settings in targets/{self.target}.yml, or the sign-in, have a problem "
                "(details above). If it says you are not signed in, run: databricks auth login "
                f"--host {host or '<your workspace URL>'}"
            )
        try:
            config: dict[str, Any] = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise DeployError("The CLI's validation output was not JSON; update the CLI.") from error
        check_config(config, self.target)
        values = variable_values(config)
        app_name = config["resources"]["apps"]["idp_app"]["name"]
        self.say(f"  Workspace  {config.get('workspace', {}).get('host')}")
        self.say(
            f"  Data       {values['catalog']}.{values['project_schema']}, tables "
            f"{values['table_prefix']}_*"
        )
        self.say(f"  Warehouse  {values['warehouse_id']}")
        self.say(f"  Chat       {values['chat_mode']}")
        self.say(f"  App        {app_name}")
        return config

    def app_exists(self, app_name: str) -> bool:
        return self.cli("apps", "get", app_name, "-o", "json", capture=True).returncode == 0

    def app_url(self, app_name: str) -> str:
        result = self.cli("apps", "get", app_name, "-o", "json", capture=True)
        try:
            url = json.loads(result.stdout).get("url") if result.returncode == 0 else None
        except json.JSONDecodeError:
            url = None
        return str(url or f"(see Compute > Apps > {app_name})")

    # Helpers -----------------------------------------------------------------------------------

    def step(self, text: str) -> None:
        self.steps += 1
        self.say(f"\n[{self.steps}] {text}")

    def cli(self, *args: str, capture: bool = False, profile: bool = True) -> Result:
        command = [self.executable, *args]
        if profile and self.profile:
            command += ["-p", self.profile]
        return self.runner(command, capture)

    def bundle(self, *args: str, capture: bool = False) -> Result:
        return self.cli("bundle", *args, "-t", self.target, capture=capture)

    def settings_host(self) -> str | None:
        try:
            text = (TARGETS_DIR / f"{self.target}.yml").read_text(encoding="utf-8")
        except OSError:
            return None
        match = re.search(r"^\s*host:\s*(\S+)", text, re.MULTILINE)
        return match.group(1) if match else None


def variable_values(config: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, variable in (config.get("variables") or {}).items():
        value = variable.get("value", variable.get("default")) if isinstance(variable, dict) else None
        values[name] = "" if value is None else str(value).strip()
    return values


def check_config(config: dict[str, Any], target: str) -> None:
    """What the CLI cannot know: the values this app needs to work, in plain words."""
    where = f"targets/{target}.yml"
    if (config.get("bundle") or {}).get("mode") == "development":
        raise DeployError(f"{where} must use mode: production (development mode renames everything).")
    values = variable_values(config)
    for name in IDENTIFIER_VARIABLES:
        if SIMPLE_IDENTIFIER.fullmatch(values.get(name, "")) is None:
            raise DeployError(
                f"{name} in {where} must be a single name of letters, digits and underscores."
            )
    if not values.get("warehouse_id") or values["warehouse_id"].startswith("YOUR"):
        raise DeployError(f"Set warehouse_id in {where} to your SQL warehouse's id.")
    if values.get("catalog", "").startswith("YOUR"):
        raise DeployError(f"Set catalog in {where} to an existing catalog.")
    if values.get("chat_mode") not in CHAT_MODES:
        raise DeployError(
            f'chat_mode in {where} must be knowledge_assistant, supervisor or "off" (in quotes).'
        )
    app_name = ((config.get("resources") or {}).get("apps") or {}).get("idp_app", {}).get("name")
    if not isinstance(app_name, str) or APP_NAME.fullmatch(app_name) is None:
        raise DeployError(
            f"The App name {app_name!r} must be lowercase letters, digits and hyphens, at most "
            f"30 characters: change deployment_name in {where}."
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", help="settings file name in databricks_etl/targets, without .yml")
    parser.add_argument("--profile", help="Databricks CLI profile, when several match the host")
    parser.add_argument(
        "--adopt-app",
        action="store_true",
        help="once: take over an existing App named deployment_name, keeping its address",
    )
    args = parser.parse_args(argv)
    executable = shutil.which("databricks")
    if executable is None:
        print(
            "The Databricks CLI is not installed or not on PATH. Install it: "
            "https://docs.databricks.com/dev-tools/cli/install.html",
            file=sys.stderr,
        )
        return 1
    deployer = Deployer(
        args.target, profile=args.profile, executable=executable, adopt_app=args.adopt_app
    )
    try:
        url = deployer.deploy()
    except DeployError as error:
        print(f"\nStopped: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Running this command again is safe.", file=sys.stderr)
        return 130
    print(f"\nDone. The app is at {url}")
    print("The first chat index can take a while; until then chat may answer that it has no documents.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
