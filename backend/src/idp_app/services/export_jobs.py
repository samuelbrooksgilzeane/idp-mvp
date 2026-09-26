"""Finite export worker. No AI calls; inputs are immutable retained extraction results."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from idp_app.services.export_artifacts import ExportArtifacts
from idp_app.services.export_requests import ExportRequests
from idp_app.services.export_service import ExportService
from idp_app.services.export_writer import WRITER_VERSION, write_export


def run_export(
    requests: ExportRequests,
    sources: ExportService,
    artifacts: ExportArtifacts,
    identity: str,
    *,
    max_disk_bytes: int = 2_000_000_000,
) -> None:
    row = requests.get(identity)
    if row is None:
        raise ValueError("Unknown export request")
    if row["expires_at"] <= datetime.now(UTC).isoformat():
        artifacts.delete(identity)
        requests.save({**row, "state": "EXPIRED"})
        return
    if row["state"] == "SUCCEEDED":
        return
    if row["writer_version"] != WRITER_VERSION:
        requests.save({**row, "state": "FAILED", "error": "Writer changed; create a new export."})
        return
    requests.repair_members(row)
    row.update(state="RUNNING", runs_processed=0, error=None)
    requests.save(row)
    try:
        with TemporaryDirectory(prefix="idp-export-worker-") as directory:
            destination = Path(directory) / "artifact"

            def tables():
                for table in sources.iter_tables(row["run_ids"]):
                    yield table
                    row["runs_processed"] += 1
                    if row["runs_processed"] % 25 == 0:
                        requests.save(row)

            multiple = write_export(
                tables(), destination, row["format"], max_disk_bytes=max_disk_bytes
            )
            size, checksum = artifacts.publish(identity, destination)
            is_zip = multiple or row["format"] == "csv"
            row.update(
                state="SUCCEEDED",
                bytes=size,
                checksum=checksum,
                filename="extraction-results.zip" if is_zip else "extraction-results.xlsx",
                media_type="application/zip"
                if is_zip
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                completed_at=datetime.now(UTC).isoformat(),
            )
            requests.save(row)
    except Exception as error:
        row.update(state="FAILED", error=str(error)[:500])
        requests.save(row)
        raise


def build_exports(settings: Any):
    from idp_app.api.dependencies import build_export_service
    from idp_app.core.config import IdpMode
    from idp_app.services.document_registry import DatabricksDocumentRegistry

    if settings.mode is IdpMode.MOCK:
        requests = ExportRequests(settings.local_data_dir / "registry.sqlite3")
        artifacts = ExportArtifacts(str(settings.local_data_dir / "artifacts"))
    else:
        from databricks.sdk import WorkspaceClient

        client = WorkspaceClient()
        sql = DatabricksDocumentRegistry(
            client,
            settings.warehouse_id,
            settings.catalog,
            settings.project_schema,
            settings.table_prefix,
        )
        namespace = f"{settings.catalog}.{settings.project_schema}.{settings.table_prefix}"
        requests = ExportRequests(sql=sql, namespace=namespace)
        artifacts = ExportArtifacts(
            f"/Volumes/{settings.catalog}/{settings.project_schema}/{settings.artifacts_volume_name}",
            client,
        )
    return requests, build_export_service(settings), artifacts
