"""One claimed immutable extraction; only the inference callback uses Spark AI."""

import argparse
import json
from uuid import UUID

from work_runtime import build_extraction


def main():
    parser = argparse.ArgumentParser()
    for name in (
        "catalog",
        "project-schema",
        "table-prefix",
        "source-volume-name",
        "artifacts-volume-name",
        "warehouse-id",
        "dispatch-id",
        "work-item-id",
    ):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    UUID(args.dispatch_id)
    UUID(args.work_item_id)
    queue, _ = build_extraction(
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
    from idp_app.services.extraction_execution import execute_extraction
    from pyspark.sql import SparkSession

    session = SparkSession.builder.getOrCreate()
    table = f"{args.catalog}.{args.project_schema}.{args.table_prefix}_parsed_documents"

    def infer(document, parse, schema):
        result = session.sql(
            f"""
            SELECT TO_JSON(ai_extract(parsed, :schema_json, options => map(
                'version', '2.1', 'mode', 'precision', 'enableCitations', 'true',
                'enableConfidenceScores', 'true', 'instructions', :instructions))) AS result_json
            FROM {table} WHERE parse_run_id = :parse_id AND document_id = :document_id
                AND content_sha256 = :content_hash AND status = 'SUCCESS' LIMIT 1
        """,
            args={
                "schema_json": json.dumps(
                    {
                        key: field.model_dump(exclude_none=True)
                        for key, field in schema.ai_extract_schema.items()
                    }
                ),
                "instructions": schema.instructions,
                "parse_id": parse.parse_run_id,
                "document_id": document.document_id,
                "content_hash": document.content_sha256,
            },
        ).first()
        if result is None or result["result_json"] is None:
            raise RuntimeError("ai_extract returned no result")
        return json.loads(result["result_json"])

    execute_extraction(queue, args.dispatch_id, args.work_item_id, infer)


if __name__ == "__main__":
    main()
