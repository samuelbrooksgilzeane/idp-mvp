from pathlib import Path

import pytest
import yaml

from idp_app.core.data_objects import TABLE_NAMES, VIEW_NAMES, DataObjectNamespace

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "databricks_etl" / "sql" / "create_objects.sql"
BUNDLE_RESOURCE = ROOT / "databricks_etl" / "resources" / "bootstrap.job.yml"


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
    assert "WHERE STATUS = 'PARSED'" in sql
    assert "'_LATEST_SUCCESSFUL_EXTRACTIONS'" in sql
    assert "WHERE STATUS = 'EXTRACTED'" in sql
    assert "'_VALIDATION_SUMMARY'" in sql
    assert "COUNT_IF(STATUS = 'PASS') AS PASS_COUNT" in sql


def test_bundle_bootstrap_uses_only_trusted_parameters() -> None:
    resource = yaml.safe_load(BUNDLE_RESOURCE.read_text(encoding="utf-8"))
    task = resource["resources"]["jobs"]["governed_data_bootstrap"]["tasks"][0]
    sql_task = task["sql_task"]

    assert sql_task["warehouse_id"] == "${var.warehouse_id}"
    assert sql_task["file"]["path"] == "../sql/create_objects.sql"
    assert sql_task["parameters"] == {
        "catalog": "${var.catalog}",
        "project_schema": "${var.project_schema}",
        "table_prefix": "${var.table_prefix}",
        "source_volume_name": "${var.source_volume_name}",
        "artifacts_volume_name": "${var.artifacts_volume_name}",
    }
