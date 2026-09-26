"""Explicit, bounded backfill. Run only for specified successful parse IDs; no AI calls."""
import argparse
from uuid import UUID
from work_runtime import build_preparation


def main():
    parser = argparse.ArgumentParser()
    for name in ("catalog", "project-schema", "table-prefix", "source-volume-name",
                 "artifacts-volume-name", "warehouse-id"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--parse-run-id", action="append", required=True)
    values = vars(parser.parse_args())
    identities = [str(UUID(identity)) for identity in values.pop("parse_run_id")]
    if len(identities) > 25:
        raise ValueError("Backfill at most 25 explicit parse IDs per invocation")
    preparation, _ = build_preparation(**values)
    from idp_app.services.viewer_projection import ViewerProjection
    projection = ViewerProjection(sql=preparation.documents,
        namespace=f"{values['catalog']}.{values['project_schema']}.{values['table_prefix']}")
    for identity in identities:
        run = preparation.runs.get(identity)
        if run is None:
            raise ValueError(f"Unknown parse: {identity}")
        projection.build(run)


if __name__ == "__main__":
    main()
