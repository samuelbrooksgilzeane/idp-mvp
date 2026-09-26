# Databricks notebook source
# This loader is a notebook task because taskValues is a notebook utility.
import json
import sys

repository_root = dbutils.widgets.get("repository_root")  # noqa: F821
sys.path.insert(0, f"{repository_root}/databricks_etl/src")
from work_runtime import build_preparation, build_extraction  # noqa: E402
from idp_app.services.work_dispatch import manifest_ids  # noqa: E402

from work_limits import validate_capacity  # noqa: E402

validate_capacity(
    int(dbutils.widgets.get("parse_concurrency")),  # noqa: F821
    int(dbutils.widgets.get("extraction_concurrency")),  # noqa: F821
    int(dbutils.widgets.get("combined_budget")),  # noqa: F821
)  # noqa: F821

dispatch_id = dbutils.widgets.get("dispatch_id")  # noqa: F821
legacy = json.loads(dbutils.widgets.get("inputs"))  # noqa: F821
if dispatch_id and legacy:
    raise ValueError("Choose a dispatch manifest or legacy inputs, never both")
ids = []
if dispatch_id:
    kind = dbutils.widgets.get("work_kind")  # noqa: F821
    if kind not in {"PARSE", "EXTRACT"}:
        raise ValueError("Unknown work kind")
    build = build_preparation if kind == "PARSE" else build_extraction
    preparation, _ = build(
        **{
            key: dbutils.widgets.get(key)  # noqa: F821
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
    dispatch = preparation.work.dispatch(dispatch_id)
    if dispatch is None or dispatch.kind != kind:
        raise ValueError("Dispatch manifest not found")
    ids = manifest_ids(dispatch, preparation.work)
dbutils.jobs.taskValues.set(key="work_item_ids", value=ids)  # noqa: F821
