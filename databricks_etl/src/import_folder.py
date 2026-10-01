"""Register the PDFs of one folder-import batch from the import volume.

Reuses the app's upload-batch contract (the same claims, dedupe and outcomes as a browser upload),
so re-running a batch is safe: finished items are skipped and failed ones retried."""

import argparse
import logging

import work_runtime  # noqa: F401  Puts the backend package on the import path.


def main():
    parser = argparse.ArgumentParser()
    for name in (
        "batch-id",
        "catalog",
        "project-schema",
        "table-prefix",
        "source-volume-name",
        "artifacts-volume-name",
        "import-volume-name",
        "warehouse-id",
    ):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--concurrency", type=int, default=8, choices=range(1, 33))
    parser.add_argument(
        "--auto-prepare-enabled", default="false", choices=["true", "false"]
    )
    parser.add_argument("--dispatch-job-id", type=int)
    parser.add_argument("--ka-sync-enabled", default="false", choices=["true", "false"])
    parser.add_argument("--ka-id", default=" ")
    args = parser.parse_args()
    if not args.batch_id.strip():
        raise ValueError("Start folder imports from the app; the run needs a batch_id")
    logging.basicConfig(level=logging.INFO)

    from idp_app.api.dependencies import (
        build_document_service,
        build_folder_import_service,
        build_upload_batch_service,
    )
    from idp_app.core.config import IdpMode, Settings

    auto_prepare = args.auto_prepare_enabled == "true"
    settings = Settings(
        _env_file=None,
        mode=IdpMode.DATABRICKS,
        catalog=args.catalog,
        project_schema=args.project_schema,
        table_prefix=args.table_prefix,
        source_volume_name=args.source_volume_name,
        artifacts_volume_name=args.artifacts_volume_name,
        import_volume_name=args.import_volume_name,
        warehouse_id=args.warehouse_id,
        # Required by Settings but unused here: this run registers documents only.
        parse_job_id=1,
        extraction_job_id=1,
        import_job_id=1,
        # Registered documents request preparation exactly as a browser upload does.
        auto_prepare_enabled=auto_prepare,
        dispatch_job_id=args.dispatch_job_id if auto_prepare else None,
        import_concurrency=args.concurrency,
        ka_sync_enabled=args.ka_sync_enabled == "true",
        ka_id=args.ka_id,
    )
    uploads = build_upload_batch_service(settings, build_document_service(settings))
    service = build_folder_import_service(settings, uploads, start_runs=False)
    try:
        counts = service.run(args.batch_id)
    finally:
        if settings.ka_sync_enabled and settings.ka_id:
            from idp_app.services.chat_sync import shared_knowledge_sync

            # The process exits next, so sync now instead of after the quiet period.
            shared_knowledge_sync(settings.ka_id).flush()
    print(f"Folder import {args.batch_id}: {counts}")


if __name__ == "__main__":
    main()
