"""Grant the IDP App's service principal what it uses beyond its 20 bound resources, and let the
app users group open the App.

A Databricks App binds at most 20 resources and this App needs more, so the rest is granted here by
the deploying identity, which owns the tables, volumes, Jobs and chat agents. Everything only adds
access (GRANT, permissions PATCH), so running it again is safe. Run it after the bootstrap, whose
view replacement drops grants, and after chat provisioning. The App is looked up by name at run
time: a bundle reference to it would make this Job wait on the App, which cannot deploy until the
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
CHAT_MODES = ("off", "knowledge_assistant", "supervisor")
# Tables the App reads and writes that do not fit under the binding cap.
READ_WRITE_TABLES = (
    "upload_batches",
    "upload_items",
    "work_batches",
    "work_items",
    "work_dispatches",
    "invoice_line_candidates",
)


@dataclass(frozen=True)
class Parameters:
    app_name: str
    catalog: str
    project_schema: str
    table_prefix: str
    import_volume_name: str
    import_job_id: int
    dispatch_job_id: int
    export_job_id: int
    chat_mode: str = "knowledge_assistant"
    chat_endpoint: str = ""
    app_users_group: str = ""

    def table(self, name: str) -> str:
        return f"`{self.catalog}`.`{self.project_schema}`.`{self.table_prefix}_{name}`"


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
    for name in ("import-job-id", "dispatch-job-id", "export-job-id"):
        parser.add_argument(f"--{name}", type=int, required=True)
    parser.add_argument("--chat-mode", choices=CHAT_MODES, default="knowledge_assistant")
    parser.add_argument("--chat-endpoint", default=" ")
    parser.add_argument("--app-users-group", default=" ")
    args = parser.parse_args()
    parameters = Parameters(
        app_name=args.app_name,
        catalog=args.catalog,
        project_schema=args.project_schema,
        table_prefix=args.table_prefix,
        import_volume_name=args.import_volume_name,
        import_job_id=args.import_job_id,
        dispatch_job_id=args.dispatch_job_id,
        export_job_id=args.export_job_id,
        chat_mode=args.chat_mode,
        chat_endpoint=args.chat_endpoint.strip(),
        app_users_group=args.app_users_group.strip(),
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
    table = parameters.table
    to = f"TO `{principal}`"
    return [
        # Viewer projections
        f"GRANT SELECT ON TABLE {table('parsed_page_manifest')} {to}",
        f"GRANT SELECT ON TABLE {table('parsed_page_elements')} {to}",
        # Upload batches, folder import, the work queue and invoice line items
        *(f"GRANT SELECT, MODIFY ON TABLE {table(name)} {to}" for name in READ_WRITE_TABLES),
        # Chat endpoint and Knowledge Assistant recorded by workspace_setup
        f"GRANT SELECT ON TABLE {table('app_settings')} {to}",
        # Folder import lists the import volume
        f"GRANT READ VOLUME ON VOLUME {base}.`{parameters.import_volume_name}` {to}",
        # Document chat: the Supervisor runs the functions as the caller, and history is per user
        *(f"GRANT EXECUTE ON FUNCTION {table('chat_' + name)} {to}" for name in CHAT_FUNCTIONS),
        *(f"GRANT SELECT ON TABLE {table('chat_' + name)} {to}" for name in CHAT_VIEWS),
        f"GRANT SELECT, MODIFY ON TABLE {table('chat_messages')} {to}",
    ]


def job_permissions(parameters: Parameters) -> list[Permission]:
    """Folder import, the work dispatcher and the exporter; the App starts each."""
    job_ids = (parameters.import_job_id, parameters.dispatch_job_id, parameters.export_job_id)
    return [Permission("jobs", str(job_id), "CAN_MANAGE_RUN") for job_id in job_ids]


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


def chat_permissions(api: Any, chat_endpoint: str | None, ka_id: str | None) -> list[Permission]:
    """What the App needs to chat through ``chat_endpoint`` and to sync the Knowledge Assistant.

    CAN_MANAGE on the KA lets the App request a Sync, and CAN_EDIT on an agent's experiment lets
    it tag answer traces. Agent permissions do not reach their serving endpoints, so each endpoint
    also needs CAN_QUERY. A Supervisor is only looked up when chat goes to an endpoint other than
    the KA's own (supervisor mode, or the chat_endpoint override)."""
    permissions: list[Permission] = []
    endpoints: list[str] = []
    ka_endpoint = None
    if ka_id:
        ka = next(
            (
                item
                for item in list_all(api, "/api/2.1/knowledge-assistants", "knowledge_assistants")
                if item.get("id") == ka_id
            ),
            None,
        )
        if ka is None:
            raise ValueError(f"Knowledge Assistant {ka_id} not found; run workspace_setup again")
        permissions.append(Permission("knowledge-assistants", ka_id, "CAN_MANAGE"))
        if ka.get("experiment_id"):
            permissions.append(Permission("experiments", ka["experiment_id"], "CAN_EDIT"))
        ka_endpoint = ka.get("endpoint_name")
        if ka_endpoint:
            endpoints.append(ka_endpoint)
    if chat_endpoint and chat_endpoint != ka_endpoint:
        endpoints.append(chat_endpoint)
        supervisors = [
            item
            for item in list_all(api, "/api/2.1/supervisor-agents", "supervisor_agents")
            if item.get("endpoint_name") == chat_endpoint
        ]
        if len(supervisors) > 1:
            raise ValueError(f"More than one Supervisor Agent serves {chat_endpoint}")
        if supervisors:
            supervisor_permissions, sub_endpoints = supervisor_access(api, supervisors[0])
            permissions += supervisor_permissions
            endpoints += sub_endpoints
    for name in dict.fromkeys(endpoints):
        endpoint = api.do("GET", f"/api/2.0/serving-endpoints/{name}")
        permissions.append(Permission("serving-endpoints", endpoint["id"], "CAN_QUERY"))
    return list(dict.fromkeys(permissions))


def supervisor_access(
    api: Any, supervisor: dict[str, Any]
) -> tuple[list[Permission], list[str]]:
    """The Supervisor, its experiment and each Knowledge Assistant it calls, plus their
    serving endpoints."""
    supervisor_id = supervisor["supervisor_agent_id"]
    permissions = [Permission("supervisor-agents", supervisor_id, "CAN_QUERY")]
    if supervisor.get("experiment_id"):
        permissions.append(Permission("experiments", supervisor["experiment_id"], "CAN_EDIT"))
    endpoints: list[str] = []
    for tool in list_all(api, f"/api/2.1/supervisor-agents/{supervisor_id}/tools", "tools"):
        assistant = tool.get("knowledge_assistant") or {}
        if assistant.get("knowledge_assistant_id"):
            permissions.append(
                Permission("knowledge-assistants", assistant["knowledge_assistant_id"], "CAN_MANAGE")
            )
        if assistant.get("serving_endpoint_name"):
            endpoints.append(assistant["serving_endpoint_name"])
    return permissions, endpoints


def recorded_settings(parameters: Parameters) -> dict[str, str]:
    """The values workspace_setup's provision_chat task wrote to <prefix>_app_settings."""
    rows = spark.sql(  # type: ignore[name-defined]  # noqa: F821 - Databricks injects Spark.
        f"SELECT name, value FROM {parameters.table('app_settings')}"
    ).collect()
    return {row["name"]: row["value"] for row in rows if row["value"]}


def apply_permission(
    api: Any, permission: Permission, principal: str, kind: str = "service_principal_name"
) -> None:
    api.do(
        "PATCH",
        f"/api/2.0/permissions/{permission.object_type}/{permission.object_id}",
        body={"access_control_list": [{kind: principal, "permission_level": permission.level}]},
    )


def main() -> None:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.errors import NotFound

    parameters = parse_arguments()
    client = WorkspaceClient()
    try:
        app = client.apps.get(parameters.app_name)
    except NotFound:
        # A new workspace runs the setup before the App can deploy; the deploy runs this Job
        # again once the App exists.
        print(f"App {parameters.app_name} does not exist yet; nothing to grant")
        return
    principal = app.service_principal_client_id
    if not principal:
        raise SystemExit(f"App {parameters.app_name} has no service principal yet")

    for statement in sql_grants(parameters, principal):
        spark.sql(statement)  # type: ignore[name-defined]  # noqa: F821 - Databricks injects Spark.
        print(statement)
    api = client.api_client
    permissions = job_permissions(parameters)
    if parameters.chat_mode != "off":
        recorded = recorded_settings(parameters)
        endpoint = parameters.chat_endpoint or recorded.get("chat_endpoint")
        if endpoint or recorded.get("ka_id"):
            permissions += chat_permissions(api, endpoint, recorded.get("ka_id"))
        else:
            print("Chat is not provisioned yet; run workspace_setup to provision and grant it")
    for permission in permissions:
        apply_permission(api, permission, principal)
        print(f"{permission.level} on {permission.object_type}/{permission.object_id}")
    if parameters.app_users_group:
        # PATCH only adds: owners and other grants on the App are kept.
        apply_permission(
            api,
            Permission("apps", parameters.app_name, "CAN_USE"),
            parameters.app_users_group,
            "group_name",
        )
        print(f"CAN_USE on apps/{parameters.app_name} for group {parameters.app_users_group}")


if __name__ == "__main__":
    main()
