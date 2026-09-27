"""Render offline by default; explicit apply creates once, or reconciles reviewed drift.

Deployment state must be retained per workspace/project and kept out of git.
Never invoked by app startup. No prompts, SQL, indexing or volume attachment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from string import Template
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def digest(definition: str) -> str:
    canonical = json.dumps(
        json.loads(definition), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def render(catalog: str, schema: str, prefix: str) -> str:
    values = {"catalog": catalog, "project_schema": schema, "table_prefix": prefix}
    if any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", v) for v in values.values()
    ):
        raise ValueError("Project names must be simple SQL identifiers")
    return Template(
        (ROOT / "databricks_etl/genie/project.geniespace.json").read_text()
    ).substitute(values)


def save(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def reconcile(
    client: Any,
    state_path: Path,
    definition: str,
    warehouse: str,
    title: str,
    reviewed_hash: str | None = None,
) -> str:
    # Exclusive lock prevents concurrent deployers from both creating a space.
    lock = state_path.with_suffix(state_path.suffix + ".lock")
    with lock.open("x"):
        try:
            host = client.config.host.rstrip("/")
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
            if state and (
                state.get("host") != host or state.get("warehouse_id") != warehouse
            ):
                raise ValueError(
                    "Deployment state belongs to a different workspace or warehouse"
                )
            if state.get("pending_create"):
                raise ValueError(
                    "Uncertain previous create: locate the space and adopt its ID before retrying"
                )
            if not state:
                state = {
                    "host": host,
                    "warehouse_id": warehouse,
                    "pending_create": True,
                }
                save(state_path, state)  # Durable intent BEFORE network mutation.
                created = client.genie.create_space(
                    warehouse_id=warehouse, serialized_space=definition, title=title
                )
                if not created.space_id:
                    raise ValueError(
                        "Create returned no ID; reconcile pending state manually"
                    )
                state = {
                    "host": host,
                    "warehouse_id": warehouse,
                    "space_id": created.space_id,
                    "definition_hash": digest(definition),
                }
                save(state_path, state)
                return str(created.space_id)
            space_id = state["space_id"]
            remote = client.genie.get_space(space_id, include_serialized_space=True)
            if not remote.serialized_space:
                raise ValueError("Remote definition unavailable; refusing update")
            snapshot = state_path.with_suffix(".remote.json")
            snapshot.write_text(remote.serialized_space)
            remote_hash = digest(remote.serialized_space)
            if remote_hash != digest(definition):
                if reviewed_hash != remote_hash:
                    raise ValueError(
                        f"Review {snapshot}, reconcile local definition, then supply "
                        f"--reviewed-remote-hash {remote_hash}"
                    )
                if not remote.etag:
                    raise ValueError(
                        "Remote ETag missing; refusing unprotected overwrite"
                    )
                client.genie.update_space(
                    space_id, serialized_space=definition, etag=remote.etag
                )
            state["definition_hash"] = digest(definition)
            save(state_path, state)
            return str(space_id)
        finally:
            lock.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("catalog", "schema", "prefix", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--profile", default="idp-mvp")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--warehouse")
    parser.add_argument("--title", default="IDP project")
    parser.add_argument("--reviewed-remote-hash")
    args = parser.parse_args()
    definition = render(args.catalog, args.schema, args.prefix)
    Path(args.output).write_text(definition)
    print("Rendered definition:", digest(definition))
    if args.apply:
        if not args.state or not args.warehouse:
            parser.error("--apply requires --state and --warehouse")
        from databricks.sdk import WorkspaceClient

        print(
            "Space ID:",
            reconcile(
                WorkspaceClient(profile=args.profile),
                args.state,
                definition,
                args.warehouse,
                args.title,
                args.reviewed_remote_hash,
            ),
        )


if __name__ == "__main__":
    main()
