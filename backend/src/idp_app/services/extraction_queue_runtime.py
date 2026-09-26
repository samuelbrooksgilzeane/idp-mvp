"""Build the same extraction queue for the API, coordinator and tasks."""

from idp_app.core.config import IdpMode, Settings
from idp_app.services.document_registry import DatabricksDocumentRegistry, SQLiteDocumentRegistry
from idp_app.services.extraction_batches import (
    DatabricksExtractionBatchRepository,
    ExtractionBatchService,
    SQLiteExtractionBatchRepository,
)
from idp_app.services.extraction_queue import ExtractionQueue
from idp_app.services.extraction_runs import (
    DatabricksExtractionRunRepository,
    SQLiteExtractionRunRepository,
)
from idp_app.services.parse_runs import DatabricksParseRunRepository, SQLiteParseRunRepository
from idp_app.services.schema_registry import DatabricksSchemaRepository, SQLiteSchemaRepository
from idp_app.services.schemas import load_source_manifests
from idp_app.services.work_batches import DatabricksWorkRepository, SQLiteWorkRepository


def build_extraction_queue(settings: Settings) -> ExtractionQueue:
    if settings.mode is IdpMode.MOCK:
        path = settings.local_data_dir / "registry.sqlite3"
        schemas = SQLiteSchemaRepository(path)
        for manifest in load_source_manifests():
            schemas.register(manifest, "source-controlled-bootstrap")
        return ExtractionQueue(
            ExtractionBatchService(
                SQLiteExtractionBatchRepository(path),
                SQLiteDocumentRegistry(path),
                SQLiteParseRunRepository(path),
                schemas,
                str(path.resolve()),
            ),
            SQLiteWorkRepository(path),
            SQLiteExtractionRunRepository(path),
        )
    from databricks.sdk import WorkspaceClient

    assert (
        settings.warehouse_id
        and settings.catalog
        and settings.project_schema
        and settings.table_prefix
    )
    documents = DatabricksDocumentRegistry(
        WorkspaceClient(),
        settings.warehouse_id,
        settings.catalog,
        settings.project_schema,
        settings.table_prefix,
    )
    args = (documents, settings.catalog, settings.project_schema, settings.table_prefix)
    namespace = f"{settings.catalog}.{settings.project_schema}.{settings.table_prefix}"
    return ExtractionQueue(
        ExtractionBatchService(
            DatabricksExtractionBatchRepository(documents, namespace),
            documents,
            DatabricksParseRunRepository(*args),
            DatabricksSchemaRepository(*args),
            namespace,
        ),
        DatabricksWorkRepository(documents, namespace),
        DatabricksExtractionRunRepository(*args),
    )
