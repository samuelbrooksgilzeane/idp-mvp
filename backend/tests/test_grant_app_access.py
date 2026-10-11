"""Offline checks of the App access grants Job; no Spark or workspace."""

import importlib.util
import sys
from pathlib import Path
from typing import Any

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
KA = "0a1b2c3d-0000-4000-8000-000000000001"


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.pages = {
            "/api/2.1/knowledge-assistants": [
                {"knowledge_assistants": [
                    {"id": "other", "endpoint_name": "ka-other-endpoint"},
                    {"id": KA, "endpoint_name": "ka-1-endpoint", "experiment_id": "88"},
                ]},
            ],
            "/api/2.1/supervisor-agents": [
                {
                    "supervisor_agents": [{"endpoint_name": "other", "supervisor_agent_id": "x"}],
                    "next_page_token": "p2",
                },
                {"supervisor_agents": [{"endpoint_name": "mas-1-endpoint",
                                        "supervisor_agent_id": "sup-1", "experiment_id": "77"}]},
            ],
            "/api/2.1/supervisor-agents/sup-1/tools": [
                {"tools": [
                    {
                        "tool_type": "knowledge_assistant",
                        "knowledge_assistant": {
                            "knowledge_assistant_id": "ka-1",
                            "serving_endpoint_name": "ka-1-endpoint",
                        },
                    },
                    {"tool_type": "uc_function", "uc_function": {"name": "c.s.f"}},
                ]},
            ],
        }

    def do(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append(path)
        if path.startswith("/api/2.0/serving-endpoints/"):
            return {"id": f"id-{path.rsplit('/', 1)[1]}"}
        token = (kwargs.get("query") or {}).get("page_token")
        return self.pages[path][1 if token else 0]


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
    assert f"GRANT READ VOLUME ON VOLUME {volume} TO `{PRINCIPAL}`" in statements
    assert sum("EXECUTE ON FUNCTION" in s for s in statements) == 5
    assert statements[-1].startswith(
        "GRANT SELECT, MODIFY ON TABLE `workspace`.`idp_mvp`.`idp_dev_chat_messages`"
    )


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


def test_knowledge_assistant_mode_needs_no_supervisor_lookup() -> None:
    api = FakeApi()
    permissions = task.chat_permissions(api, "ka-1-endpoint", KA)
    assert [(p.object_type, p.object_id, p.level) for p in permissions] == [
        ("knowledge-assistants", KA, "CAN_MANAGE"),
        ("experiments", "88", "CAN_EDIT"),
        ("serving-endpoints", "id-ka-1-endpoint", "CAN_QUERY"),
    ]
    assert "/api/2.1/supervisor-agents" not in api.calls


def test_supervisor_endpoint_follows_the_supervisor_to_its_knowledge_assistant() -> None:
    permissions = task.chat_permissions(FakeApi(), "mas-1-endpoint", None)
    assert [(p.object_type, p.object_id, p.level) for p in permissions] == [
        ("supervisor-agents", "sup-1", "CAN_QUERY"),
        ("experiments", "77", "CAN_EDIT"),
        ("knowledge-assistants", "ka-1", "CAN_MANAGE"),
        ("serving-endpoints", "id-mas-1-endpoint", "CAN_QUERY"),
        ("serving-endpoints", "id-ka-1-endpoint", "CAN_QUERY"),
    ]


def test_override_endpoint_is_granted_besides_the_provisioned_knowledge_assistant() -> None:
    permissions = task.chat_permissions(FakeApi(), "custom-agent", KA)
    assert [(p.object_type, p.object_id) for p in permissions] == [
        ("knowledge-assistants", KA),
        ("experiments", "88"),
        ("serving-endpoints", "id-ka-1-endpoint"),
        ("serving-endpoints", "id-custom-agent"),
    ]


def test_unknown_knowledge_assistant_fails_instead_of_granting_nothing() -> None:
    with pytest.raises(ValueError, match="not found"):
        task.chat_permissions(FakeApi(), None, "0a1b2c3d-0000-4000-8000-000000000999")


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
