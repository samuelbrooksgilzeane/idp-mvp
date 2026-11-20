"""Grant the IDP App's service principal what it uses beyond its 20 bound resources, and let the
app users group open the App.

A Databricks App binds at most 20 resources and this App needs more, so the rest is granted here by
the deploying identity, which owns the tables, volumes and Jobs. Everything only adds access
(GRANT, permissions PATCH), so running it again is safe. Run it after the bootstrap, whose view
replacement drops grants. The App is looked up by name at run
time: a bundle reference to it would make this Job wait on the App, which cannot deploy until the
bootstrap has created its tables.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from typing import Any

SIMPLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
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
        # Folder import lists the import volume
        f"GRANT READ VOLUME ON VOLUME {base}.`{parameters.import_volume_name}` {to}",
    ]


def job_permissions(parameters: Parameters) -> list[Permission]:
    """Folder import, the work dispatcher and the exporter; the App starts each."""
    job_ids = (parameters.import_job_id, parameters.dispatch_job_id, parameters.export_job_id)
    return [Permission("jobs", str(job_id), "CAN_MANAGE_RUN") for job_id in job_ids]


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
    for permission in job_permissions(parameters):
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
