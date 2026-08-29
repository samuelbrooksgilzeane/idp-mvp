import json
from pathlib import Path

import pytest
import yaml

from idp_app.core.data_objects import TABLE_NAMES, VIEW_NAMES, DataObjectNamespace

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "databricks_etl" / "sql" / "create_objects.sql"
PARSING_MIGRATION = ROOT / "databricks_etl" / "sql" / "migrate_parsing.sql"
EXTRACTION_MIGRATION = ROOT / "databricks_etl" / "sql" / "migrate_extraction.sql"
BUNDLE_RESOURCE = ROOT / "databricks_etl" / "resources" / "bootstrap.job.yml"
APP_RESOURCE = ROOT / "databricks_etl" / "resources" / "application.app.yml"
BUNDLE_CONFIG = ROOT / "databricks_etl" / "databricks.yml"
SCHEMA_REGISTRATION = ROOT / "databricks_etl" / "src" / "register_schemas.py"
EXTRACTION_JOB = ROOT / "databricks_etl" / "resources" / "extraction.job.yml"
EXTRACTION_SOURCE = ROOT / "databricks_etl" / "src" / "extract_document.py"
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
    assert tasks[2]["task_key"] == "migrate_extraction_columns"
    assert tasks[2]["depends_on"] == [{"task_key": "migrate_parsing_columns"}]
    assert tasks[2]["sql_task"]["file"]["path"] == "../sql/migrate_extraction.sql"
    assert tasks[3]["task_key"] == "register_production_schemas"
    assert tasks[3]["depends_on"] == [{"task_key": "migrate_extraction_columns"}]
    assert tasks[3]["spark_python_task"]["python_file"] == "../src/register_schemas.py"
    assert tasks[3]["spark_python_task"]["parameters"][-1] == (
        "${workspace.file_path}/schemas/invoice_v1.json"
    )
    assert tasks[3]["environment_key"] == "default"


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


def test_extraction_schema_migration_is_guarded_and_non_destructive() -> None:
    sql = " ".join(EXTRACTION_MIGRATION.read_text(encoding="utf-8").upper().split())

    assert sql.count("IF NOT EXISTS") == 1
    assert "INFORMATION_SCHEMA.COLUMNS" in sql
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
    assert env["IDP_EXTRACTION_JOB_ID"]["value_from"] == "document-extractor"

    bindings = {item["name"]: item for item in app["resources"]}
    assert bindings["sql-warehouse"]["sql_warehouse"] == {
        "id": "${var.warehouse_id}",
        "permission": "CAN_USE",
    }
    assert bindings["document-parser"]["job"] == {
        "id": "${resources.jobs.document_parser.id}",
        "permission": "CAN_MANAGE_RUN",
    }
    assert bindings["document-extractor"]["job"] == {
        "id": "${resources.jobs.document_extractor.id}",
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


def test_extraction_job_pins_evidence_contract_and_retains_raw_first() -> None:
    resource = yaml.safe_load(EXTRACTION_JOB.read_text(encoding="utf-8"))
    job = resource["resources"]["jobs"]["document_extractor"]
    for_each = job["tasks"][0]["for_each_task"]
    task = for_each["task"]["spark_python_task"]
    source = EXTRACTION_SOURCE.read_text(encoding="utf-8")

    assert task["python_file"] == "../src/extract_document.py"
    # Per-document values arrive as for_each inputs; only trusted configuration is a job
    # parameter, so the browser can never supply an identifier or table name.
    assert set(item["name"] for item in job["parameters"]) == {
        "catalog",
        "project_schema",
        "table_prefix",
        "inputs",
    }
    assert for_each["inputs"] == "{{job.parameters.inputs}}"
    # for_each defaults to concurrency 1, which would process a batch sequentially.
    assert for_each["concurrency"] == "${var.batch_concurrency}"
    assert [
        parameter for parameter in task["parameters"] if parameter.startswith("{{input.")
    ] == [
        "{{input.document_id}}",
        "{{input.extraction_run_id}}",
        "{{input.schema_id}}",
        "{{input.schema_version}}",
        "{{input.requested_by}}",
    ]
    for required in (
        "ai_extract(",
        "'version', '2.1'",
        "'mode', 'precision'",
        "'enableCitations', 'true'",
        "'enableConfidenceScores', 'true'",
        "SET ai_result = PARSE_JSON(:result_json)",
        "ORDER BY completed_at DESC, parse_run_id DESC",
        "computed_hash",
        "flatten_fields(",
    ):
        assert required in source
    raw_write = source.index("SET ai_result = PARSE_JSON(:result_json)")
    flatten = source.index("fields = flatten_fields(")
    assert raw_write < flatten
    for forbidden in (" DROP ", " TRUNCATE ", " DELETE "):
        assert forbidden not in f" {' '.join(source.upper().split())} "


def test_bundle_sync_includes_app_source_and_built_frontend() -> None:
    config = yaml.safe_load(BUNDLE_CONFIG.read_text(encoding="utf-8"))

    assert config["sync"]["paths"] == [".."]
    assert config["sync"]["include"] == ["../frontend/dist/**"]
