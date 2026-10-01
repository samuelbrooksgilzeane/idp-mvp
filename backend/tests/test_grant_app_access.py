"""Offline checks of the App access grants Job; no Spark or workspace."""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

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
    chat_endpoint="mas-1-endpoint",
)


class FakeApi:
    def __init__(self) -> None:
        self.pages = {
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
    volume = "`workspace`.`idp_mvp`.`idp_import`"
    assert f"GRANT READ VOLUME ON VOLUME {volume} TO `{PRINCIPAL}`" in statements
    assert sum("EXECUTE ON FUNCTION" in s for s in statements) == 5
    assert statements[-1].startswith(
        "GRANT SELECT, MODIFY ON TABLE `workspace`.`idp_mvp`.`idp_dev_chat_messages`"
    )


def test_principal_must_be_an_application_id() -> None:
    with pytest.raises(ValueError):
        task.sql_grants(PARAMETERS, "x` TO `everyone")


def test_chat_permissions_follow_the_supervisor_to_its_knowledge_assistant() -> None:
    permissions = task.chat_permissions(FakeApi(), "mas-1-endpoint")
    assert [(p.object_type, p.object_id, p.level) for p in permissions] == [
        ("supervisor-agents", "sup-1", "CAN_QUERY"),
        ("experiments", "77", "CAN_EDIT"),
        ("knowledge-assistants", "ka-1", "CAN_MANAGE"),
        ("serving-endpoints", "id-mas-1-endpoint", "CAN_QUERY"),
        ("serving-endpoints", "id-ka-1-endpoint", "CAN_QUERY"),
    ]


def test_unknown_chat_endpoint_fails_instead_of_granting_nothing() -> None:
    with pytest.raises(ValueError, match="Expected one Supervisor"):
        task.chat_permissions(FakeApi(), "missing-endpoint")
