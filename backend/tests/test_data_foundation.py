import json
from pathlib import Path

import pytest
import yaml

from idp_app.core.data_objects import TABLE_NAMES, VIEW_NAMES, DataObjectNamespace

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "databricks_etl" / "sql" / "create_objects.sql"
PARSING_MIGRATION = ROOT / "databricks_etl" / "sql" / "migrate_parsing.sql"
BUNDLE_RESOURCE = ROOT / "databricks_etl" / "resources" / "bootstrap.job.yml"
APP_RESOURCE = ROOT / "databricks_etl" / "resources" / "application.app.yml"
BUNDLE_CONFIG = ROOT / "databricks_etl" / "databricks.yml"
SCHEMA_REGISTRATION = ROOT / "databricks_etl" / "src" / "register_schemas.py"
INVOICE_MANIFEST = ROOT / "schemas" / "invoice_v1.json"


def test_object_names_resolve_only_to_configured_namespace() -> None:
    namespace = DataObjectNamespace("governed", "idp_project", "idp_dev")

    assert len(namespace.tables) == len(TABLE_NAMES)
    assert len(namespace.views) == len(VIEW_NAMES)
    assert all(name.startswith("governed.idp_project.idp_dev_") for name in namespace.tables)
    assert all(".default." not in name for name in namespace.tables + namespace.views)


def test_dev_and_prod_prefixes_are_distinct() -> None:
    dev = DataObjectNamespace("governed", "idp_project", "idp_dev")
    prod = DataObjectNamespace("governed", "idp_project", "idp")

    assert set(dev.tables).isdisjoint(prod.tables)
    assert set(dev.views).isdisjoint(prod.views)


def test_unknown_object_name_is_rejected() -> None:
    namespace = DataObjectNamespace("governed", "idp_project", "idp_dev")

    with pytest.raises(ValueError, match="Unknown governed data object"):
        namespace.object_name("browser_supplied_table")


def test_migration_is_idempotent_prefixed_and_non_destructive() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    normalized = " ".join(sql.upper().split())

    assert normalized.count("CREATE TABLE IF NOT EXISTS") == len(TABLE_NAMES)
    assert normalized.count("CREATE VOLUME IF NOT EXISTS") == 2
    assert "CREATE SCHEMA IF NOT EXISTS" in normalized
    assert normalized.count("CREATE OR REPLACE VIEW") == len(VIEW_NAMES)
    assert "CREATE CATALOG" not in normalized
    assert " DROP " not in f" {normalized} "
    assert " TRUNCATE " not in f" {normalized} "
    assert ".DEFAULT." not in normalized

    for name in TABLE_NAMES + VIEW_NAMES:
        assert f":TABLE_PREFIX || '_{name.upper()}'" in normalized


def test_views_declare_stable_empty_result_schemas() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").upper()

    assert "'_LATEST_SUCCESSFUL_PARSES'" in sql
    assert "WHERE STATUS = 'SUCCESS'" in sql
    assert "'_LATEST_SUCCESSFUL_EXTRACTIONS'" in sql
    assert "WHERE STATUS = 'EXTRACTED'" in sql
    assert "'_VALIDATION_SUMMARY'" in sql
    assert "COUNT_IF(STATUS = 'PASS') AS PASS_COUNT" in sql


def test_bundle_bootstrap_uses_only_trusted_parameters() -> None:
    resource = yaml.safe_load(BUNDLE_RESOURCE.read_text(encoding="utf-8"))
    tasks = resource["resources"]["jobs"]["governed_data_bootstrap"]["tasks"]
    sql_task = tasks[0]["sql_task"]

    assert sql_task["warehouse_id"] == "${var.warehouse_id}"
    assert sql_task["file"]["path"] == "../sql/create_objects.sql"
    assert sql_task["parameters"] == {
        "catalog": "${var.catalog}",
        "project_schema": "${var.project_schema}",
        "table_prefix": "${var.table_prefix}",
        "source_volume_name": "${var.source_volume_name}",
        "artifacts_volume_name": "${var.artifacts_volume_name}",
    }
    assert tasks[1]["depends_on"] == [{"task_key": "create_governed_objects"}]
    assert tasks[1]["sql_task"]["file"]["path"] == "../sql/migrate_parsing.sql"
    assert tasks[1]["sql_task"]["parameters"] == {
        "catalog": "${var.catalog}",
        "project_schema": "${var.project_schema}",
        "table_prefix": "${var.table_prefix}",
    }
    assert tasks[2]["task_key"] == "register_production_schemas"
    assert tasks[2]["depends_on"] == [{"task_key": "migrate_parsing_columns"}]
    assert tasks[2]["spark_python_task"]["python_file"] == "../src/register_schemas.py"
    assert tasks[2]["spark_python_task"]["parameters"][-1] == (
        "${workspace.file_path}/schemas/invoice_v1.json"
    )
    assert tasks[2]["environment_key"] == "default"


def test_parsing_schema_migration_is_guarded_and_non_destructive() -> None:
    sql = " ".join(PARSING_MIGRATION.read_text(encoding="utf-8").upper().split())

    assert sql.count("IF NOT EXISTS") == 3
    assert "INFORMATION_SCHEMA.COLUMNS" in sql
    assert "ADD COLUMN CONTENT_SHA256" in sql
    assert "ADD COLUMN REQUESTED_BY" in sql
    assert "ADD COLUMN JOB_RUN_ID" in sql
    assert " DROP " not in f" {sql} "
    assert " TRUNCATE " not in f" {sql} "
    assert " DELETE " not in f" {sql} "


def test_databricks_app_uses_trusted_configuration_and_resource_bindings() -> None:
    resource = yaml.safe_load(APP_RESOURCE.read_text(encoding="utf-8"))
    app = resource["resources"]["apps"]["idp_app"]

    assert app["source_code_path"] == "../.."
    assert app["name"] == "${var.app_name}-${bundle.target}"

    env = {item["name"]: item for item in app["config"]["env"]}
    assert env["IDP_MODE"]["value"] == "databricks"
    assert env["IDP_CATALOG"]["value"] == "${var.catalog}"
    assert env["IDP_PROJECT_SCHEMA"]["value"] == "${var.project_schema}"
    assert env["IDP_TABLE_PREFIX"]["value"] == "${var.table_prefix}"
    assert env["IDP_WAREHOUSE_ID"]["value_from"] == "sql-warehouse"
    assert env["IDP_PARSE_JOB_ID"]["value_from"] == "document-parser"

    bindings = {item["name"]: item for item in app["resources"]}
    assert bindings["sql-warehouse"]["sql_warehouse"] == {
        "id": "${var.warehouse_id}",
        "permission": "CAN_USE",
    }
    assert bindings["document-parser"]["job"] == {
        "id": "${resources.jobs.document_parser.id}",
        "permission": "CAN_MANAGE_RUN",
    }
    assert bindings["source-volume-write"]["uc_securable"]["permission"] == (
        "WRITE_VOLUME"
    )
    assert bindings["documents-table-modify"]["uc_securable"]["permission"] == (
        "MODIFY"
    )
    assert bindings["schema-registry-table-select"]["uc_securable"] == {
        "securable_type": "TABLE",
        "securable_full_name": (
            "${var.catalog}.${var.project_schema}.${var.table_prefix}_schema_registry"
        ),
        "permission": "SELECT",
    }


def test_schema_registration_task_is_immutable_and_source_controlled() -> None:
    registration = SCHEMA_REGISTRATION.read_text(encoding="utf-8")
    normalized = " ".join(registration.upper().split())
    manifest = json.loads(INVOICE_MANIFEST.read_text(encoding="utf-8"))

    assert manifest["status"] == "PRODUCTION"
    assert "WHEN NOT MATCHED THEN INSERT" in normalized
    assert "SCHEMA_HASH" in normalized
    assert "INCREMENT SCHEMA_VERSION" in normalized
    assert " DELETE " not in f" {normalized} "
    assert " DROP " not in f" {normalized} "
    assert " TRUNCATE " not in f" {normalized} "


def test_bundle_sync_includes_app_source_and_built_frontend() -> None:
    config = yaml.safe_load(BUNDLE_CONFIG.read_text(encoding="utf-8"))

    assert config["sync"]["paths"] == [".."]
    assert config["sync"]["include"] == ["../frontend/dist/**"]
