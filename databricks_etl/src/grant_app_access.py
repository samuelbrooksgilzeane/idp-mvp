"""Grant the IDP App's service principal what it uses beyond its 20 bound resources.

A Databricks App binds at most 20 resources and this App needs more, so the rest is granted here by
the deploying identity, which owns the tables, volumes, Jobs and chat agents. Everything only adds
access (GRANT, permissions PATCH), so running it again is safe. Run it after the bootstrap, whose
view replacement drops grants, and after the first deploy. The App is looked up by name at run time:
a bundle reference to it would make this Job wait on the App, which cannot deploy until the
bootstrap has created its tables.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from typing import Any

SIMPLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
CHAT_FUNCTIONS = ("document_fields", "find_documents", "invoices", "invoice_totals", "case_documents")
CHAT_VIEWS = ("documents", "extractions", "fields", "records")


@dataclass(frozen=True)
class Parameters:
    app_name: str
    catalog: str
    project_schema: str
    table_prefix: str
    import_volume_name: str
    import_job_id: int
    chat_endpoint: str


@dataclass(frozen=True)
class Permission:
    """One permissions PATCH: /api/2.0/permissions/<object_type>/<object_id>."""

    object_type: str
    object_id: str
    level: str


def parse_arguments() -> Parameters:
    parser = argparse.ArgumentParser()
    for name in ("app-name", "catalog", "project-schema", "table-prefix", "import-volume-name"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--import-job-id", type=int, required=True)
    parser.add_argument("--chat-endpoint", default=" ")
    args = parser.parse_args()
    parameters = Parameters(
        app_name=args.app_name,
        catalog=args.catalog,
        project_schema=args.project_schema,
        table_prefix=args.table_prefix,
        import_volume_name=args.import_volume_name,
        import_job_id=args.import_job_id,
        chat_endpoint=args.chat_endpoint.strip(),
    )
    identifiers = (
        parameters.catalog,
        parameters.project_schema,
        parameters.table_prefix,
        parameters.import_volume_name,
    )
    if any(SIMPLE_IDENTIFIER.fullmatch(value) is None for value in identifiers):
        raise ValueError("Databricks object configuration contains an invalid identifier")
    return parameters


def sql_grants(parameters: Parameters, principal: str) -> list[str]:
    if not re.fullmatch(r"[0-9a-f-]{36}", principal):
        raise ValueError("The App service principal must be an application id")
    base = f"`{parameters.catalog}`.`{parameters.project_schema}`"
    table = lambda name: f"{base}.`{parameters.table_prefix}_{name}`"  # noqa: E731
    to = f"TO `{principal}`"
    return [
        # Viewer projections
        f"GRANT SELECT ON TABLE {table('parsed_page_manifest')} {to}",
        f"GRANT SELECT ON TABLE {table('parsed_page_elements')} {to}",
        # Folder import lists the import volume
        f"GRANT READ VOLUME ON VOLUME {base}.`{parameters.import_volume_name}` {to}",
        # Document chat: the Supervisor runs the functions as the caller, and history is per user
        *(f"GRANT EXECUTE ON FUNCTION {table('chat_' + name)} {to}" for name in CHAT_FUNCTIONS),
        *(f"GRANT SELECT ON TABLE {table('chat_' + name)} {to}" for name in CHAT_VIEWS),
        f"GRANT SELECT, MODIFY ON TABLE {table('chat_messages')} {to}",
    ]


def list_all(api: Any, path: str, key: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    query: dict[str, Any] = {"page_size": 100}
    for _ in range(50):  # bounded pagination
        page = api.do("GET", path, query=query) or {}
        items.extend(page.get(key, []))
        token = page.get("next_page_token")
        if not token:
            return items
        query = {"page_size": 100, "page_token": token}
    raise ValueError(f"Too many pages listing {path}")


def chat_permissions(api: Any, chat_endpoint: str) -> list[Permission]:
    """The App queries the Supervisor behind ``chat_endpoint``; the Supervisor calls its
    Knowledge Assistant. Agent permissions do not reach their serving endpoints, so both
    endpoints need CAN_QUERY as well. CAN_MANAGE on the KA lets the App request a Sync, and
    CAN_EDIT on the Supervisor's experiment lets it tag answer traces."""
    supervisors = [
        item
        for item in list_all(api, "/api/2.1/supervisor-agents", "supervisor_agents")
        if item.get("endpoint_name") == chat_endpoint
    ]
    if len(supervisors) != 1:
        raise ValueError(f"Expected one Supervisor Agent serving {chat_endpoint}")
    supervisor = supervisors[0]
    supervisor_id = supervisor["supervisor_agent_id"]
    endpoints = [chat_endpoint]
    permissions = [Permission("supervisor-agents", supervisor_id, "CAN_QUERY")]
    if supervisor.get("experiment_id"):
        permissions.append(Permission("experiments", supervisor["experiment_id"], "CAN_EDIT"))
    tools = list_all(api, f"/api/2.1/supervisor-agents/{supervisor_id}/tools", "tools")
    for tool in tools:
        assistant = tool.get("knowledge_assistant") or {}
        if assistant.get("knowledge_assistant_id"):
            permissions.append(
                Permission("knowledge-assistants", assistant["knowledge_assistant_id"], "CAN_MANAGE")
            )
        if assistant.get("serving_endpoint_name"):
            endpoints.append(assistant["serving_endpoint_name"])
    for name in endpoints:
        endpoint = api.do("GET", f"/api/2.0/serving-endpoints/{name}")
        permissions.append(Permission("serving-endpoints", endpoint["id"], "CAN_QUERY"))
    return permissions


def apply_permission(api: Any, permission: Permission, principal: str) -> None:
    api.do(
        "PATCH",
        f"/api/2.0/permissions/{permission.object_type}/{permission.object_id}",
        body={
            "access_control_list": [
                {"service_principal_name": principal, "permission_level": permission.level}
            ]
        },
    )


def main() -> None:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.errors import NotFound

    parameters = parse_arguments()
    client = WorkspaceClient()
    try:
        app = client.apps.get(parameters.app_name)
    except NotFound:
        # A new workspace runs the bootstrap before the App can deploy; `make deploy-first`
        # runs this Job again once the App exists.
        print(f"App {parameters.app_name} does not exist yet; nothing to grant")
        return
    principal = app.service_principal_client_id
    if not principal:
        raise SystemExit(f"App {parameters.app_name} has no service principal yet")

    for statement in sql_grants(parameters, principal):
        spark.sql(statement)  # type: ignore[name-defined]  # noqa: F821 - Databricks injects Spark.
        print(statement)
    api = client.api_client
    permissions = [Permission("jobs", str(parameters.import_job_id), "CAN_MANAGE_RUN")]
    if parameters.chat_endpoint:
        permissions += chat_permissions(api, parameters.chat_endpoint)
    for permission in permissions:
        apply_permission(api, permission, principal)
        print(f"{permission.level} on {permission.object_type}/{permission.object_id}")


if __name__ == "__main__":
    main()
