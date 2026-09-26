"""Databricks Job task for one immutable document parse attempt."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, replace

SIMPLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


@dataclass(frozen=True)
class Parameters:
    catalog: str
    project_schema: str
    table_prefix: str
    source_volume_name: str
    artifacts_volume_name: str
    document_id: str
    parse_run_id: str
    source_path: str
    image_output_path: str
    work_item_id: str | None = None
    dispatch_id: str | None = None
    warehouse_id: str | None = None
    viewer_projection_enabled: str = "false"


def parse_arguments() -> Parameters:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--project-schema", required=True)
    parser.add_argument("--table-prefix", required=True)
    parser.add_argument("--source-volume-name", required=True)
    parser.add_argument("--artifacts-volume-name", required=True)
    parser.add_argument("--document-id", default="")
    parser.add_argument("--parse-run-id", default="")
    parser.add_argument("--source-path", default="")
    parser.add_argument("--image-output-path", default="")
    parser.add_argument("--work-item-id")
    parser.add_argument("--dispatch-id")
    parser.add_argument("--warehouse-id")
    parser.add_argument("--viewer-projection-enabled", default="false")
    arguments = parser.parse_args()
    parameters = Parameters(
        catalog=arguments.catalog,
        project_schema=arguments.project_schema,
        table_prefix=arguments.table_prefix,
        source_volume_name=arguments.source_volume_name,
        artifacts_volume_name=arguments.artifacts_volume_name,
        document_id=arguments.document_id,
        parse_run_id=arguments.parse_run_id,
        source_path=arguments.source_path,
        image_output_path=arguments.image_output_path,
        work_item_id=arguments.work_item_id,
        dispatch_id=arguments.dispatch_id,
        warehouse_id=arguments.warehouse_id,
        viewer_projection_enabled=arguments.viewer_projection_enabled,
    )
    validate(parameters)
    return parameters


def validate(parameters: Parameters) -> None:
    identifiers = (
        parameters.catalog,
        parameters.project_schema,
        parameters.table_prefix,
        parameters.source_volume_name,
        parameters.artifacts_volume_name,
    )
    if any(SIMPLE_IDENTIFIER.fullmatch(value) is None for value in identifiers):
        raise ValueError(
            "Databricks object configuration contains an invalid identifier"
        )
    if parameters.work_item_id or parameters.dispatch_id:
        if (
            not parameters.warehouse_id
            or not parameters.work_item_id
            or not parameters.dispatch_id
        ):
            raise ValueError(
                "Manifest execution requires work-item-id, dispatch-id and warehouse-id"
            )
        if (
            UUID.fullmatch(parameters.work_item_id) is None
            or UUID.fullmatch(parameters.dispatch_id) is None
        ):
            raise ValueError("Work and dispatch identities must be UUIDs")
        if any(
            (
                parameters.document_id,
                parameters.parse_run_id,
                parameters.source_path,
                parameters.image_output_path,
            )
        ):
            raise ValueError(
                "Manifest tasks must resolve trusted inputs, not accept legacy paths"
            )
        return
    if UUID.fullmatch(parameters.document_id) is None:
        raise ValueError("document_id must be a UUID")
    if UUID.fullmatch(parameters.parse_run_id) is None:
        raise ValueError("parse_run_id must be a UUID")

    source_root = (
        f"/Volumes/{parameters.catalog}/{parameters.project_schema}/"
        f"{parameters.source_volume_name}/incoming/"
    )
    image_root = (
        f"/Volumes/{parameters.catalog}/{parameters.project_schema}/"
        f"{parameters.artifacts_volume_name}/page_images/"
        f"{parameters.document_id}/{parameters.parse_run_id}/"
    )
    if not parameters.source_path.startswith(
        source_root
    ) or ".." in parameters.source_path.split("/"):
        raise ValueError("source_path is outside the configured incoming directory")
    if not parameters.source_path.lower().endswith(".pdf"):
        raise ValueError("source_path must identify a PDF")
    if parameters.image_output_path != image_root:
        raise ValueError(
            "image_output_path is outside the parse run artifact directory"
        )


def qualified(parameters: Parameters, suffix: str) -> str:
    return (
        f"`{parameters.catalog}`.`{parameters.project_schema}`."
        f"`{parameters.table_prefix}_{suffix}`"
    )


def main() -> None:
    parameters = parse_arguments()
    from pyspark.sql import SparkSession

    global spark
    spark = SparkSession.builder.getOrCreate()
    preparation = None
    claimed = None
    retained = None
    if parameters.work_item_id:
        from work_runtime import build_preparation
        from idp_app.services.work_execution import (
            begin_parse,
            check_owner,
            finish_parse,
        )

        preparation, _ = build_preparation(
            parameters.catalog,
            parameters.project_schema,
            parameters.table_prefix,
            parameters.source_volume_name,
            parameters.artifacts_volume_name,
            parameters.warehouse_id,
        )
        claimed = begin_parse(
            preparation, parameters.dispatch_id, parameters.work_item_id
        )
        if claimed is None:
            return
        retained = claimed.run.parsed
        parameters = replace(
            parameters,
            document_id=claimed.document.document_id,
            parse_run_id=claimed.run.parse_run_id,
            source_path=claimed.document.source_path,
            image_output_path=claimed.run.page_image_root.rstrip("/") + "/",
            work_item_id=None,
            dispatch_id=None,
        )
        validate(parameters)

    def checkpoint():
        if preparation is not None and claimed is not None:
            check_owner(preparation, claimed)

    documents = qualified(parameters, "documents")
    parse_runs = qualified(parameters, "parsed_documents")

    result_persisted = retained is not None
    try:
        checkpoint()
        if retained is not None:
            parse_result = {"parsed_json": json.dumps(retained)}
        else:
            parse_result = spark.sql(  # type: ignore[name-defined]  # Databricks injects SparkSession.
                """
                SELECT to_json(
                  ai_parse_document(
                    content,
                    map(
                      'version', '2.0',
                      'imageOutputPath', :image_output_path,
                      'descriptionElementTypes', ''
                    )
                  )
                ) AS parsed_json
                FROM READ_FILES(:source_path, format => 'binaryFile')
                """,
                args={
                    "source_path": parameters.source_path,
                    "image_output_path": parameters.image_output_path,
                },
            ).first()
        if parse_result is None or parse_result["parsed_json"] is None:
            raise RuntimeError("ai_parse_document returned no result")

        checkpoint()
        # Retain the complete parser contract before any derived fields are written.
        spark.sql(  # type: ignore[name-defined]
            f"""
            UPDATE {parse_runs}
            SET parsed = parse_json(:parsed_json)
            WHERE parse_run_id = :parse_run_id AND status = 'RUNNING'
            """,
            args={
                "parse_run_id": parameters.parse_run_id,
                "parsed_json": parse_result["parsed_json"],
            },
        )

        result_persisted = True

        spark.sql(  # type: ignore[name-defined]
            f"""
            UPDATE {parse_runs}
            SET
              document_text = array_join(
                transform(
                  variant_get(parsed, '$.document.elements', 'array<variant>'),
                  element -> coalesce(variant_get(element, '$.content', 'string'), '')
                ),
                '\n\n'
              ),
              page_count = size(
                variant_get(parsed, '$.document.pages', 'array<variant>')
              ),
              parse_error = CASE
                WHEN size(variant_get(parsed, '$.error_status', 'array<variant>')) > 0
                  THEN parse_json(to_json(
                    variant_get(parsed, '$.error_status', 'array<variant>')
                  ))
                ELSE NULL
              END,
              status = CASE
                WHEN size(variant_get(parsed, '$.error_status', 'array<variant>')) > 0
                  THEN 'FAILED'
                ELSE 'SUCCESS'
              END,
              completed_at = CURRENT_TIMESTAMP()
            WHERE parse_run_id = :parse_run_id AND status = 'RUNNING'
            """,
            args={"parse_run_id": parameters.parse_run_id},
        )

        spark.sql(  # type: ignore[name-defined]
            f"""
            UPDATE {documents}
            SET status = CASE
                  WHEN (SELECT status FROM {parse_runs}
                        WHERE parse_run_id = :parse_run_id) = 'SUCCESS'
                    THEN 'PARSED'
                  ELSE 'PARSE_FAILED'
                END,
                updated_at = CURRENT_TIMESTAMP()
            WHERE document_id = :document_id AND status = 'PARSING'
            """,
            args={
                "document_id": parameters.document_id,
                "parse_run_id": parameters.parse_run_id,
            },
        )
        if preparation is not None and claimed is not None:
            finish_parse(preparation, claimed)
        if parameters.viewer_projection_enabled.lower() == "true":
            try:
                from work_runtime import build_preparation
                from idp_app.services.viewer_projection import ViewerProjection
                projection_source, _ = build_preparation(
                    catalog=parameters.catalog, project_schema=parameters.project_schema,
                    table_prefix=parameters.table_prefix, source_volume_name=parameters.source_volume_name,
                    artifacts_volume_name=parameters.artifacts_volume_name, warehouse_id=parameters.warehouse_id)
                run = projection_source.runs.get(parameters.parse_run_id)
                if run is not None and run.status == "SUCCESS":
                    ViewerProjection(sql=projection_source.documents,
                        namespace=f"{parameters.catalog}.{parameters.project_schema}.{parameters.table_prefix}").build(run)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Viewer projection requires retained-parse backfill")

    except Exception as error:
        checkpoint()
        if result_persisted:
            raise  # Dispatcher completes retained output without another inference call.
        transient = any(
            code in str(error).upper()
            for code in (
                "TEMPORARILY_UNAVAILABLE",
                "RESOURCE_EXHAUSTED",
                "TIMEOUT",
                "HTTP 429",
                "HTTP 503",
            )
        )
        failure = json.dumps(
            {
                "code": "TRANSIENT_PARSE_ERROR" if transient else "PARSE_FAILED",
                "error_message": "Document parsing failed in Databricks.",
            }
        )
        spark.sql(  # type: ignore[name-defined]
            f"""
            UPDATE {parse_runs}
            SET parse_error = parse_json(:parse_error), status = 'FAILED',
                completed_at = CURRENT_TIMESTAMP()
            WHERE parse_run_id = :parse_run_id AND status = 'RUNNING'
            """,
            args={
                "parse_run_id": parameters.parse_run_id,
                "parse_error": failure,
            },
        )
        spark.sql(  # type: ignore[name-defined]
            f"""
            UPDATE {documents}
            SET status = 'PARSE_FAILED', updated_at = CURRENT_TIMESTAMP()
            WHERE document_id = :document_id AND status = 'PARSING'
            """,
            args={"document_id": parameters.document_id},
        )
        raise


if __name__ == "__main__":
    main()
