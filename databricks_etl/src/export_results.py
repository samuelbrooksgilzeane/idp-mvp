"""Explicit, serialized retained-result export; never calls parsing or extraction AI."""
import argparse
from uuid import UUID
from work_runtime import build_preparation


def main():
    parser = argparse.ArgumentParser()
    for name in ("catalog", "project-schema", "table-prefix", "source-volume-name",
                 "artifacts-volume-name", "warehouse-id", "export-id"):
        parser.add_argument(f"--{name}", required=True)
    values = vars(parser.parse_args())
    identity = str(UUID(values.pop("export_id")))
    preparation, _ = build_preparation(**values)
    from idp_app.services.export_jobs import build_exports, run_export
    requests, sources, artifacts = build_exports(preparation.settings)
    run_export(requests, sources, artifacts, identity)


if __name__ == "__main__":
    main()
