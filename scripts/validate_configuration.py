from pathlib import Path
from typing import Any

import yaml

from idp_app.core.config import IdpMode, Settings

ROOT = Path(__file__).resolve().parents[1]
TRUSTED_VARIABLES = {
    "catalog",
    "project_schema",
    "table_prefix",
    "source_volume_name",
    "artifacts_volume_name",
    "warehouse_id",
    "validation_endpoint",
    "evaluation_experiment",
    "app_name",
}
EXPECTED_BOOTSTRAP_PARAMETERS = {
    "catalog": "${var.catalog}",
    "project_schema": "${var.project_schema}",
    "table_prefix": "${var.table_prefix}",
    "source_volume_name": "${var.source_volume_name}",
    "artifacts_volume_name": "${var.artifacts_volume_name}",
}


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a YAML mapping")
    return data


def validate_app_config() -> None:
    config = load_yaml(ROOT / "app.yaml")
    command = config.get("command")
    if not isinstance(command, list) or "idp_app.main:create_app" not in command:
        raise ValueError("app.yaml must start the IDP FastAPI application factory")

    serialized = yaml.safe_dump(config).lower()
    for forbidden in ("token", "password", "client_secret"):
        if forbidden in serialized:
            raise ValueError(f"app.yaml must not contain {forbidden}")


def validate_bundle_config() -> None:
    config = load_yaml(ROOT / "databricks_etl" / "databricks.yml")
    variables = config.get("variables")
    if not isinstance(variables, dict) or set(variables) != TRUSTED_VARIABLES:
        raise ValueError("databricks.yml trusted variables do not match the technical contract")

    targets = config.get("targets")
    if not isinstance(targets, dict) or set(targets) != {"dev", "prod"}:
        raise ValueError("databricks.yml must define exactly dev and prod targets")
    if targets["dev"]["variables"]["table_prefix"] != "idp_dev":
        raise ValueError("dev must use the idp_dev table prefix")
    if targets["prod"]["variables"]["table_prefix"] != "idp":
        raise ValueError("prod must use the idp table prefix")

    serialized = yaml.safe_dump(config).lower()
    if "workspace:" in serialized or "host:" in serialized:
        raise ValueError("databricks.yml must not hardcode workspace configuration")

    includes = config.get("include")
    if includes != ["resources/*.yml"]:
        raise ValueError("databricks.yml must include the reviewed bundle resources")


def validate_data_bootstrap() -> None:
    resource = load_yaml(ROOT / "databricks_etl" / "resources" / "bootstrap.job.yml")
    jobs = resource.get("resources", {}).get("jobs", {})
    bootstrap = jobs.get("governed_data_bootstrap")
    if not isinstance(bootstrap, dict):
        raise ValueError("Bundle must define the governed_data_bootstrap job")

    tasks = bootstrap.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 1:
        raise ValueError("Governed data bootstrap must contain exactly one reviewed SQL task")
    sql_task = tasks[0].get("sql_task", {})
    if sql_task.get("warehouse_id") != "${var.warehouse_id}":
        raise ValueError("Governed data bootstrap must use the trusted warehouse variable")
    if sql_task.get("parameters") != EXPECTED_BOOTSTRAP_PARAMETERS:
        raise ValueError("Governed data bootstrap parameters must match the trusted contract")

    sql = (ROOT / "databricks_etl" / "sql" / "create_objects.sql").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(sql.upper().split())
    for forbidden in ("CREATE CATALOG", " DROP ", " TRUNCATE "):
        if forbidden in f" {normalized} ":
            raise ValueError(f"Data bootstrap contains destructive or forbidden SQL: {forbidden}")
    if ".DEFAULT." in normalized:
        raise ValueError("Data bootstrap must not create objects in the default schema")


def main() -> None:
    validate_app_config()
    validate_bundle_config()
    validate_data_bootstrap()
    if Settings.model_fields["mode"].default is not IdpMode.MOCK:
        raise ValueError("Default local application mode must be mock")
    print("Configuration and YAML validation passed.")


if __name__ == "__main__":
    main()
