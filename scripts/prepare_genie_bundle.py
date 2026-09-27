"""Prepare an opt-in native Genie bundle overlay; never deploy or run indexing.

Run before every bundle plan/deploy. Existing spaces must be exported afresh, preserving
user curation and carrying an ETag. First creation requires explicit --new-space.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "databricks_etl/resources/genie.generated.yml"


def overlay(remote: Any | None) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "title": "${var.genie_project_name}",
        "warehouse_id": "${var.warehouse_id}",
        "lifecycle": {"prevent_destroy": True},
    }
    if remote is None:
        definition = (ROOT / "databricks_etl/genie/project_genie_definition.json").read_text()
    else:
        if not remote.serialized_space or not remote.etag:
            raise ValueError(
                "Export and ETag required; refusing to prepare a destructive replacement"
            )
        definition = remote.serialized_space
        resource["etag"] = remote.etag
        # Preserve human-edited metadata too; no permissions are replaced by this overlay.
        if remote.title:
            resource["title"] = remote.title
        if remote.description:
            resource["description"] = remote.description
        if remote.warehouse_id:
            resource["warehouse_id"] = remote.warehouse_id
    json.loads(definition)
    resource["serialized_space"] = definition
    return {
        "bundle": {"engine": "direct"},
        "variables": {
            "genie_space_id": {"default": "${resources.genie_spaces.project_genie.id}"},
            "genie_workspace_origin": {"default": "${workspace.host}"},
            "genie_enabled": {"default": "true"},
        },
        "resources": {"genie_spaces": {"project_genie": resource}},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--new-space", action="store_true")
    mode.add_argument("--space-id")
    parser.add_argument("--profile", default="idp-mvp")
    parser.add_argument("--host", required=True, help="Expected workspace origin")
    args = parser.parse_args()
    # Remove stale configuration before authentication: a failed refresh cannot leave a
    # previously valid-looking export behind for a subsequent deployment.
    OVERLAY.unlink(missing_ok=True)
    remote = None
    if args.space_id:
        from databricks.sdk import WorkspaceClient

        client = WorkspaceClient(profile=args.profile)
        if client.config.host.rstrip("/") != args.host.rstrip("/"):
            parser.error("Profile does not match the requested workspace")
        remote = client.genie.get_space(args.space_id, include_serialized_space=True)
        if remote.space_id != args.space_id:
            parser.error("Exported space does not match the requested ID")
    data = overlay(remote)
    # Pin host as well as origin so the artifact cannot silently target a different workspace.
    data["workspace"] = {"host": args.host.rstrip("/")}
    OVERLAY.write_text(yaml.safe_dump(data, sort_keys=False))
    print(f"Prepared {OVERLAY}. Review bundle plan before deployment.")
    print("Do not commit this remote export. Regenerate before every deployment.")
    if args.new_space:
        print(
            "First creation only: after deployment delete the overlay and record/bind the ID."
        )
    else:
        print(
            "Ensure project_genie is bound to this exact space ID in the selected target state."
        )


if __name__ == "__main__":
    main()
