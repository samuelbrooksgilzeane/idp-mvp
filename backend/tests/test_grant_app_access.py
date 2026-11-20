"""Offline checks of the App access grants Job; no Spark or workspace."""

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from idp_app.core.data_objects import TABLE_NAMES

ROOT = Path(__file__).resolve().parents[2]
PRINCIPAL = "e77ceaac-6b84-4a26-8d39-bb38d180bbc9"


def load_task():
    name = "grant_app_access"
    spec = importlib.util.spec_from_file_location(name, ROOT / "databricks_etl/src" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


task = load_task()
PARAMETERS = task.Parameters(
    app_name="idp-mvp-dev",
    catalog="workspace",
    project_schema="idp_mvp",
    table_prefix="idp_dev",
    import_volume_name="idp_import",
    import_job_id=42,
    dispatch_job_id=43,
    export_job_id=44,
)
def test_sql_grants_cover_every_overflow_object_with_quoted_names() -> None:
    statements = task.sql_grants(PARAMETERS, PRINCIPAL)
    assert statements[0] == (
        "GRANT SELECT ON TABLE `workspace`.`idp_mvp`.`idp_dev_parsed_page_manifest` "
        f"TO `{PRINCIPAL}`"
    )
    assert (
        "GRANT SELECT, MODIFY ON TABLE `workspace`.`idp_mvp`.`idp_dev_upload_batches` "
        f"TO `{PRINCIPAL}`"
    ) in statements
    volume = "`workspace`.`idp_mvp`.`idp_import`"
    assert statements[-1] == f"GRANT READ VOLUME ON VOLUME {volume} TO `{PRINCIPAL}`"
    assert not any("chat_" in s or "app_settings" in s for s in statements)


def test_principal_must_be_an_application_id() -> None:
    with pytest.raises(ValueError):
        task.sql_grants(PARAMETERS, "x` TO `everyone")


def test_every_table_the_app_uses_is_bound_or_granted() -> None:
    """A new table in TABLE_NAMES must get an App resource binding or a direct grant."""
    resource = yaml.safe_load((ROOT / "databricks_etl/resources/application.app.yml").read_text())
    prefix = "${var.catalog}.${var.project_schema}.${var.table_prefix}_"
    bound = {
        item["uc_securable"]["securable_full_name"].removeprefix(prefix)
        for item in resource["resources"]["apps"]["idp_app"]["resources"]
        if item.get("uc_securable", {}).get("securable_type") == "TABLE"
    }
    granted = {
        statement.split("`")[5].removeprefix("idp_dev_")
        for statement in task.sql_grants(PARAMETERS, PRINCIPAL)
        if " ON TABLE " in statement
    }
    assert set(TABLE_NAMES) - bound - granted == set()


def test_the_app_may_start_every_job_it_uses() -> None:
    assert [(p.object_type, p.object_id, p.level) for p in task.job_permissions(PARAMETERS)] == [
        ("jobs", "42", "CAN_MANAGE_RUN"),
        ("jobs", "43", "CAN_MANAGE_RUN"),
        ("jobs", "44", "CAN_MANAGE_RUN"),
    ]


def test_app_users_group_gets_can_use_through_an_additive_patch() -> None:
    calls = []
    api = type("Api", (), {"do": lambda self, *a, **k: calls.append((a, k))})()
    permission = task.Permission("apps", "idp-mvp-us", "CAN_USE")
    task.apply_permission(api, permission, "users", "group_name")
    assert calls == [(
        ("PATCH", "/api/2.0/permissions/apps/idp-mvp-us"),
        {"body": {"access_control_list": [
            {"group_name": "users", "permission_level": "CAN_USE"}
        ]}},
    )]
