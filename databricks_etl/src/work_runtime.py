"""Use the same queue and retry contracts in the app and Databricks tasks."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src"))


def build_preparation(
    catalog,
    project_schema,
    table_prefix,
    source_volume_name,
    artifacts_volume_name,
    warehouse_id,
    parser_job_id=1,
):
    from databricks.sdk import WorkspaceClient
    from idp_app.core.config import IdpMode, Settings
    from idp_app.services.document_registry import DatabricksDocumentRegistry
    from idp_app.services.parse_runs import DatabricksParseRunRepository
    from idp_app.services.preparation import PreparationService
    from idp_app.services.work_batches import DatabricksWorkRepository

    settings = Settings(
        _env_file=None,
        mode=IdpMode.DATABRICKS,
        catalog=catalog,
        project_schema=project_schema,
        table_prefix=table_prefix,
        source_volume_name=source_volume_name,
        artifacts_volume_name=artifacts_volume_name,
        warehouse_id=warehouse_id,
        parse_job_id=parser_job_id,
        extraction_job_id=1,
        auto_prepare_enabled=False,
        bulk_extraction_enabled=False,
    )
    client = WorkspaceClient()
    documents = DatabricksDocumentRegistry(
        client, warehouse_id, catalog, project_schema, table_prefix
    )
    runs = DatabricksParseRunRepository(
        documents, catalog, project_schema, table_prefix
    )
    return PreparationService(
        settings,
        documents,
        runs,
        DatabricksWorkRepository(
            documents, f"{catalog}.{project_schema}.{table_prefix}"
        ),
    ), client


def build_extraction(**parameters):
    from idp_app.services.extraction_queue_runtime import build_extraction_queue

    preparation, client = build_preparation(**parameters)
    return build_extraction_queue(preparation.settings), client
