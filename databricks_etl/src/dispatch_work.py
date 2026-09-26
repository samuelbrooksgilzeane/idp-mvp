"""Finite serialized drain: wake on committed work; scheduled recovery covers missed wakes."""

import argparse
import time

from work_runtime import build_preparation, build_extraction


def main():
    parser = argparse.ArgumentParser()
    for name in (
        "catalog",
        "project-schema",
        "table-prefix",
        "source-volume-name",
        "artifacts-volume-name",
        "warehouse-id",
    ):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--parser-job-id", required=True, type=int)
    parser.add_argument(
        "--extraction-enabled", default="false", choices=["true", "false"]
    )
    parser.add_argument("--extractor-job-id", type=int)
    parser.add_argument("--enabled", default="false", choices=["true", "false"])
    parser.add_argument("--dispatch-size", type=int, default=100, choices=range(1, 101))
    parser.add_argument("--max-attempts", type=int, default=3, choices=range(1, 6))
    parser.add_argument("--max-seconds", type=int, default=3600)
    args = parser.parse_args()
    if args.enabled != "true" and args.extraction_enabled != "true":
        print("Automatic preparation is disabled; no workspace calls made.")
        return
    from idp_app.services.parse_jobs import DatabricksParseJobRunner
    from idp_app.services.work_dispatch import WorkDispatcher

    preparation, client = build_preparation(
        **{
            key: getattr(args, key)
            for key in (
                "catalog",
                "project_schema",
                "table_prefix",
                "source_volume_name",
                "artifacts_volume_name",
                "warehouse_id",
                "parser_job_id",
            )
        }
    )
    dispatcher = WorkDispatcher(
        preparation,
        DatabricksParseJobRunner(client, args.parser_job_id),
        size=args.dispatch_size,
        max_attempts=args.max_attempts,
    )
    extraction_dispatcher = None
    if args.extraction_enabled == "true":
        if not args.extractor_job_id:
            raise ValueError("An extractor Job identity is required")
        from idp_app.services.extraction_manifest_jobs import (
            DatabricksExtractionManifestJobs,
        )

        extraction, _ = build_extraction(
            **{
                key: getattr(args, key)
                for key in (
                    "catalog",
                    "project_schema",
                    "table_prefix",
                    "source_volume_name",
                    "artifacts_volume_name",
                    "warehouse_id",
                )
            }
        )
        extraction_dispatcher = WorkDispatcher(
            extraction,
            DatabricksExtractionManifestJobs(client, args.extractor_job_id),
            size=args.dispatch_size,
            max_attempts=args.max_attempts,
        )
    deadline = time.monotonic() + min(max(args.max_seconds, 1), 3600)
    idle_since = None
    while time.monotonic() < deadline:
        busy = dispatcher.tick() if args.enabled == "true" else False
        if extraction_dispatcher is not None:
            busy = extraction_dispatcher.tick() or busy
        if busy:
            idle_since = None
        elif idle_since is None:
            idle_since = time.monotonic()
        elif time.monotonic() - idle_since >= 90:
            break
        time.sleep(30)


if __name__ == "__main__":
    main()
