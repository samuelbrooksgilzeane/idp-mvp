from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from idp_app.services.document_models import DocumentRecord
from idp_app.services.document_registry import DocumentRegistry
from idp_app.services.parse_runs import ParseRunRepository
from idp_app.services.preparation import PreparationService
from idp_app.services.work_batches import WorkRepository

if TYPE_CHECKING:
    from idp_app.services.batch_repository import BatchRepository
    from idp_app.services.document_chat import DocumentChatService
    from idp_app.services.folder_import import FolderImportService
    from idp_app.services.upload_batches import UploadBatchService

from databricks.sdk import WorkspaceClient
from fastapi import Request

from idp_app.core.config import IdpMode, Settings
from idp_app.services.document_registry import (
    DatabricksDocumentRegistry,
    SQLiteDocumentRegistry,
)
from idp_app.services.document_storage import (
    DatabricksVolumeStorage,
    LocalVolumeStorage,
)
from idp_app.services.documents import DocumentService, DocumentServiceError
from idp_app.services.export_service import ExportService
from idp_app.services.export_sources import (
    DatabricksExportSourceRepository,
    SQLiteExportSourceRepository,
)
from idp_app.services.extraction import ExtractionService
from idp_app.services.extraction_jobs import (
    DatabricksExtractionJobRunner,
    MockExtractionJobRunner,
)
from idp_app.services.extraction_runs import (
    DatabricksExtractionRunRepository,
    SQLiteExtractionRunRepository,
)
from idp_app.services.generic_results import ExtractionResultsService
from idp_app.services.parse_jobs import DatabricksParseJobRunner, MockParseJobRunner
from idp_app.services.parse_runs import (
    DatabricksParseRunRepository,
    SQLiteParseRunRepository,
)
from idp_app.services.parsing import ParsingService
from idp_app.services.schema_registry import (
    DatabricksSchemaRepository,
    SQLiteSchemaRepository,
)
from idp_app.services.schemas import SchemaService, load_source_manifests
from idp_app.services.validation_runs import (
    DatabricksValidationRunRepository,
    SQLiteValidationRunRepository,
)
from idp_app.services.validation_service import ValidationService
from idp_app.services.viewer import (
    DatabricksPageImageStorage,
    LocalPageImageStorage,
    ViewerService,
)


def get_document_service(request: Request) -> DocumentService:
    existing = getattr(request.app.state, "document_service", None)
    if isinstance(existing, DocumentService):
        return existing

    settings = cast(Settings, request.app.state.settings)
    service = build_document_service(settings)
    request.app.state.document_service = service
    return service


def build_document_service(settings: Settings) -> DocumentService:
    if settings.mode is IdpMode.MOCK:
        storage = LocalVolumeStorage(settings.local_data_dir)
        registry = SQLiteDocumentRegistry(settings.local_data_dir / "registry.sqlite3")
        return DocumentService(
            storage,
            registry,
            settings.max_upload_bytes,
            _registration_callback(settings, registry),
            _source_changed_callback(settings),
        )

    catalog = _required(settings.catalog, "IDP_CATALOG")
    project_schema = _required(settings.project_schema, "IDP_PROJECT_SCHEMA")
    table_prefix = _required(settings.table_prefix, "IDP_TABLE_PREFIX")
    source_volume_name = _required(settings.source_volume_name, "IDP_SOURCE_VOLUME_NAME")
    warehouse_id = _required(settings.warehouse_id, "IDP_WAREHOUSE_ID")
    try:
        client = WorkspaceClient()
    except Exception as error:
        raise DocumentServiceError(
            "DATABRICKS_AUTH_UNAVAILABLE",
            "Databricks application authentication is not available.",
            503,
        ) from error

    databricks_storage = DatabricksVolumeStorage(
        client,
        catalog,
        project_schema,
        source_volume_name,
    )
    databricks_registry = DatabricksDocumentRegistry(
        client,
        warehouse_id,
        catalog,
        project_schema,
        table_prefix,
        statement_deadline_seconds=settings.sql_statement_deadline_seconds,
    )
    return DocumentService(
        databricks_storage,
        databricks_registry,
        settings.max_upload_bytes,
        _registration_callback(settings, databricks_registry),
        _source_changed_callback(settings),
    )


def _source_changed_callback(settings: Settings) -> Callable[[], object] | None:
    if not (settings.ka_sync_enabled and settings.ka_id):
        return None
    from idp_app.services.chat_sync import shared_knowledge_sync

    return shared_knowledge_sync(settings.ka_id).request


def get_parsing_service(request: Request) -> ParsingService:
    existing = getattr(request.app.state, "parsing_service", None)
    if isinstance(existing, ParsingService):
        return existing

    settings = cast(Settings, request.app.state.settings)
    service = build_parsing_service(settings)
    request.app.state.parsing_service = service
    return service


def build_parsing_service(settings: Settings) -> ParsingService:
    if settings.mode is IdpMode.MOCK:
        database_path = settings.local_data_dir / "registry.sqlite3"
        mock_documents = SQLiteDocumentRegistry(database_path)
        mock_parse_runs = SQLiteParseRunRepository(database_path)
        mock_jobs = MockParseJobRunner(mock_parse_runs, mock_documents)
        return ParsingService(
            settings,
            mock_documents,
            mock_parse_runs,
            mock_jobs,
            _preparation(settings, mock_documents, mock_parse_runs),
        )

    catalog = _required(settings.catalog, "IDP_CATALOG")
    project_schema = _required(settings.project_schema, "IDP_PROJECT_SCHEMA")
    table_prefix = _required(settings.table_prefix, "IDP_TABLE_PREFIX")
    warehouse_id = _required(settings.warehouse_id, "IDP_WAREHOUSE_ID")
    if settings.parse_job_id is None:
        raise RuntimeError("Required trusted setting is absent: IDP_PARSE_JOB_ID")
    try:
        client = WorkspaceClient()
    except Exception as error:
        raise DocumentServiceError(
            "DATABRICKS_AUTH_UNAVAILABLE",
            "Databricks application authentication is not available.",
            503,
        ) from error

    databricks_documents = DatabricksDocumentRegistry(
        client,
        warehouse_id,
        catalog,
        project_schema,
        table_prefix,
        statement_deadline_seconds=settings.sql_statement_deadline_seconds,
    )
    databricks_parse_runs = DatabricksParseRunRepository(
        databricks_documents,
        catalog,
        project_schema,
        table_prefix,
    )
    databricks_jobs = DatabricksParseJobRunner(client, settings.parse_job_id)
    return ParsingService(
        settings,
        databricks_documents,
        databricks_parse_runs,
        databricks_jobs,
        _preparation(settings, databricks_documents, databricks_parse_runs),
    )


def get_viewer_service(request: Request) -> ViewerService:
    existing = getattr(request.app.state, "viewer_service", None)
    if isinstance(existing, ViewerService):
        return existing

    settings = cast(Settings, request.app.state.settings)
    service = build_viewer_service(settings)
    request.app.state.viewer_service = service
    return service


def build_viewer_service(settings: Settings) -> ViewerService:
    from idp_app.services.viewer_projection import ViewerProjection

    database_path = settings.local_data_dir / "registry.sqlite3"
    if settings.mode is IdpMode.MOCK:
        return ViewerService(
            SQLiteDocumentRegistry(database_path),
            SQLiteParseRunRepository(database_path),
            LocalPageImageStorage(settings.local_data_dir / "artifacts_volume" / "page_images"),
            ViewerProjection(database_path) if settings.viewer_projection_enabled else None,
        )

    catalog = _required(settings.catalog, "IDP_CATALOG")
    project_schema = _required(settings.project_schema, "IDP_PROJECT_SCHEMA")
    table_prefix = _required(settings.table_prefix, "IDP_TABLE_PREFIX")
    warehouse_id = _required(settings.warehouse_id, "IDP_WAREHOUSE_ID")
    artifacts_volume_name = _required(
        settings.artifacts_volume_name,
        "IDP_ARTIFACTS_VOLUME_NAME",
    )
    try:
        client = WorkspaceClient()
    except Exception as error:
        raise DocumentServiceError(
            "DATABRICKS_AUTH_UNAVAILABLE",
            "Databricks application authentication is not available.",
            503,
        ) from error
    documents = DatabricksDocumentRegistry(
        client,
        warehouse_id,
        catalog,
        project_schema,
        table_prefix,
        statement_deadline_seconds=settings.sql_statement_deadline_seconds,
    )
    return ViewerService(
        documents,
        DatabricksParseRunRepository(
            documents,
            catalog,
            project_schema,
            table_prefix,
        ),
        DatabricksPageImageStorage(
            client,
            catalog,
            project_schema,
            artifacts_volume_name,
        ),
        ViewerProjection(sql=documents, namespace=f"{catalog}.{project_schema}.{table_prefix}")
        if settings.viewer_projection_enabled
        else None,
    )


def get_schema_service(request: Request) -> SchemaService:
    existing = getattr(request.app.state, "schema_service", None)
    if isinstance(existing, SchemaService):
        return existing

    settings = cast(Settings, request.app.state.settings)
    service = build_schema_service(settings)
    request.app.state.schema_service = service
    return service


def build_schema_service(settings: Settings) -> SchemaService:
    if settings.mode is IdpMode.MOCK:
        repository = SQLiteSchemaRepository(settings.local_data_dir / "registry.sqlite3")
        for manifest in load_source_manifests():
            repository.register(manifest, "source-controlled-bootstrap")
        return SchemaService(repository)

    catalog = _required(settings.catalog, "IDP_CATALOG")
    project_schema = _required(settings.project_schema, "IDP_PROJECT_SCHEMA")
    table_prefix = _required(settings.table_prefix, "IDP_TABLE_PREFIX")
    warehouse_id = _required(settings.warehouse_id, "IDP_WAREHOUSE_ID")
    try:
        client = WorkspaceClient()
    except Exception as error:
        raise DocumentServiceError(
            "DATABRICKS_AUTH_UNAVAILABLE",
            "Databricks application authentication is not available.",
            503,
        ) from error
    sql_client = DatabricksDocumentRegistry(
        client,
        warehouse_id,
        catalog,
        project_schema,
        table_prefix,
        statement_deadline_seconds=settings.sql_statement_deadline_seconds,
    )
    return SchemaService(
        DatabricksSchemaRepository(
            sql_client,
            catalog,
            project_schema,
            table_prefix,
        )
    )


def get_extraction_service(request: Request) -> ExtractionService:
    existing = getattr(request.app.state, "extraction_service", None)
    if isinstance(existing, ExtractionService):
        return existing
    settings = cast(Settings, request.app.state.settings)
    service = build_extraction_service(settings)
    request.app.state.extraction_service = service
    return service


def build_extraction_service(settings: Settings) -> ExtractionService:
    database_path = settings.local_data_dir / "registry.sqlite3"
    if settings.mode is IdpMode.MOCK:
        mock_documents = SQLiteDocumentRegistry(database_path)
        mock_parse_runs = SQLiteParseRunRepository(database_path)
        mock_schemas = SQLiteSchemaRepository(database_path)
        for manifest in load_source_manifests():
            mock_schemas.register(manifest, "source-controlled-bootstrap")
        mock_runs = SQLiteExtractionRunRepository(database_path)
        mock_jobs = MockExtractionJobRunner(mock_runs, mock_documents, parse_runs=mock_parse_runs)
        return ExtractionService(
            mock_documents, mock_parse_runs, mock_schemas, mock_runs, mock_jobs
        )

    catalog = _required(settings.catalog, "IDP_CATALOG")
    project_schema = _required(settings.project_schema, "IDP_PROJECT_SCHEMA")
    table_prefix = _required(settings.table_prefix, "IDP_TABLE_PREFIX")
    warehouse_id = _required(settings.warehouse_id, "IDP_WAREHOUSE_ID")
    if settings.extraction_job_id is None:
        raise RuntimeError("Required trusted setting is absent: IDP_EXTRACTION_JOB_ID")
    try:
        client = WorkspaceClient()
    except Exception as error:
        raise DocumentServiceError(
            "DATABRICKS_AUTH_UNAVAILABLE",
            "Databricks application authentication is not available.",
            503,
        ) from error
    databricks_documents = DatabricksDocumentRegistry(
        client, warehouse_id, catalog, project_schema, table_prefix,
        statement_deadline_seconds=settings.sql_statement_deadline_seconds,
    )
    databricks_parse_runs = DatabricksParseRunRepository(
        databricks_documents, catalog, project_schema, table_prefix
    )
    databricks_schemas = DatabricksSchemaRepository(
        databricks_documents, catalog, project_schema, table_prefix
    )
    databricks_runs = DatabricksExtractionRunRepository(
        databricks_documents, catalog, project_schema, table_prefix
    )
    databricks_jobs = DatabricksExtractionJobRunner(client, settings.extraction_job_id)
    return ExtractionService(
        databricks_documents,
        databricks_parse_runs,
        databricks_schemas,
        databricks_runs,
        databricks_jobs,
    )


def get_extraction_results_service(request: Request) -> ExtractionResultsService:
    existing = getattr(request.app.state, "extraction_results_service", None)
    if isinstance(existing, ExtractionResultsService):
        return existing
    settings = cast(Settings, request.app.state.settings)
    service = build_extraction_results_service(settings)
    request.app.state.extraction_results_service = service
    return service


def build_extraction_results_service(settings: Settings) -> ExtractionResultsService:
    database_path = settings.local_data_dir / "registry.sqlite3"
    if settings.mode is IdpMode.MOCK:
        mock_schemas = SQLiteSchemaRepository(database_path)
        for manifest in load_source_manifests():
            mock_schemas.register(manifest, "source-controlled-bootstrap")
        return ExtractionResultsService(
            SQLiteExtractionRunRepository(database_path),
            mock_schemas,
            SQLiteDocumentRegistry(database_path),
        )

    catalog = _required(settings.catalog, "IDP_CATALOG")
    project_schema = _required(settings.project_schema, "IDP_PROJECT_SCHEMA")
    table_prefix = _required(settings.table_prefix, "IDP_TABLE_PREFIX")
    warehouse_id = _required(settings.warehouse_id, "IDP_WAREHOUSE_ID")
    try:
        client = WorkspaceClient()
    except Exception as error:
        raise DocumentServiceError(
            "DATABRICKS_AUTH_UNAVAILABLE",
            "Databricks application authentication is not available.",
            503,
        ) from error
    documents = DatabricksDocumentRegistry(
        client, warehouse_id, catalog, project_schema, table_prefix,
        statement_deadline_seconds=settings.sql_statement_deadline_seconds,
    )
    return ExtractionResultsService(
        DatabricksExtractionRunRepository(documents, catalog, project_schema, table_prefix),
        DatabricksSchemaRepository(documents, catalog, project_schema, table_prefix),
        documents,
    )


def get_export_service(request: Request) -> ExportService:
    existing = getattr(request.app.state, "export_service", None)
    if isinstance(existing, ExportService):
        return existing
    settings = cast(Settings, request.app.state.settings)
    service = build_export_service(settings)
    request.app.state.export_service = service
    return service


def build_export_service(settings: Settings) -> ExportService:
    database_path = settings.local_data_dir / "registry.sqlite3"
    if settings.mode is IdpMode.MOCK:
        # The bulk query joins all three registries; initialise their local tables even when
        # export is the first endpoint opened in a fresh mock environment.
        SQLiteDocumentRegistry(database_path)
        SQLiteExtractionRunRepository(database_path)
        schemas = SQLiteSchemaRepository(database_path)
        for manifest in load_source_manifests():
            schemas.register(manifest, "source-controlled-bootstrap")
        return ExportService(SQLiteExportSourceRepository(database_path))

    catalog = _required(settings.catalog, "IDP_CATALOG")
    project_schema = _required(settings.project_schema, "IDP_PROJECT_SCHEMA")
    table_prefix = _required(settings.table_prefix, "IDP_TABLE_PREFIX")
    warehouse_id = _required(settings.warehouse_id, "IDP_WAREHOUSE_ID")
    try:
        client = WorkspaceClient()
    except Exception as error:
        raise DocumentServiceError(
            "DATABRICKS_AUTH_UNAVAILABLE",
            "Databricks application authentication is not available.",
            503,
        ) from error
    sql_client = DatabricksDocumentRegistry(
        client, warehouse_id, catalog, project_schema, table_prefix,
        statement_deadline_seconds=settings.sql_statement_deadline_seconds,
    )
    return ExportService(
        DatabricksExportSourceRepository(sql_client, catalog, project_schema, table_prefix)
    )


def get_authenticated_user(request: Request) -> str:
    settings = cast(Settings, request.app.state.settings)
    for header in ("x-forwarded-email", "x-forwarded-user"):
        value = request.headers.get(header, "").strip()
        if value:
            return value[:320]
    if settings.mode is IdpMode.MOCK:
        return "local-development-user"
    raise DocumentServiceError(
        "USER_IDENTITY_MISSING",
        "Authenticated application user identity was not forwarded.",
        401,
    )


def _required(value: str | None, name: str) -> str:
    if value is None:
        raise RuntimeError(f"Required trusted setting is absent: {name}")
    return value


def get_validation_service(request: Request) -> ValidationService:
    existing = getattr(request.app.state, "validation_service", None)
    if isinstance(existing, ValidationService):
        return existing
    settings = cast(Settings, request.app.state.settings)
    service = build_validation_service(settings)
    request.app.state.validation_service = service
    return service


def build_validation_service(settings: Settings) -> ValidationService:
    database_path = settings.local_data_dir / "registry.sqlite3"
    if settings.mode is IdpMode.MOCK:
        mock_documents = SQLiteDocumentRegistry(database_path)
        mock_schemas = SQLiteSchemaRepository(database_path)
        for manifest in load_source_manifests():
            mock_schemas.register(manifest, "source-controlled-bootstrap")
        return ValidationService(
            mock_documents,
            SQLiteParseRunRepository(database_path),
            SQLiteExtractionRunRepository(database_path),
            mock_schemas,
            SQLiteValidationRunRepository(database_path),
        )

    catalog = _required(settings.catalog, "IDP_CATALOG")
    project_schema = _required(settings.project_schema, "IDP_PROJECT_SCHEMA")
    table_prefix = _required(settings.table_prefix, "IDP_TABLE_PREFIX")
    warehouse_id = _required(settings.warehouse_id, "IDP_WAREHOUSE_ID")
    try:
        client = WorkspaceClient()
    except Exception as error:
        raise DocumentServiceError(
            "DATABRICKS_AUTH_UNAVAILABLE",
            "Databricks application authentication is not available.",
            503,
        ) from error
    documents = DatabricksDocumentRegistry(
        client, warehouse_id, catalog, project_schema, table_prefix,
        statement_deadline_seconds=settings.sql_statement_deadline_seconds,
    )
    return ValidationService(
        documents,
        DatabricksParseRunRepository(documents, catalog, project_schema, table_prefix),
        DatabricksExtractionRunRepository(documents, catalog, project_schema, table_prefix),
        DatabricksSchemaRepository(documents, catalog, project_schema, table_prefix),
        DatabricksValidationRunRepository(documents, catalog, project_schema, table_prefix),
    )


def get_upload_batch_service(request: Request) -> "UploadBatchService":
    from idp_app.services.upload_batches import UploadBatchService

    existing = getattr(request.app.state, "upload_batch_service", None)
    if isinstance(existing, UploadBatchService):
        return existing
    settings = cast(Settings, request.app.state.settings)
    service = build_upload_batch_service(settings, get_document_service(request))
    request.app.state.upload_batch_service = service
    return service


def build_upload_batch_service(
    settings: Settings, documents: DocumentService
) -> "UploadBatchService":
    from idp_app.services.batch_repository import DatabricksBatchRepository, SQLiteBatchRepository
    from idp_app.services.upload_batches import UploadBatchService

    repository: BatchRepository
    if settings.mode is IdpMode.MOCK:
        repository = SQLiteBatchRepository(settings.local_data_dir / "registry.sqlite3")
    else:
        catalog = _required(settings.catalog, "IDP_CATALOG")
        schema = _required(settings.project_schema, "IDP_PROJECT_SCHEMA")
        prefix = _required(settings.table_prefix, "IDP_TABLE_PREFIX")
        sql = DatabricksDocumentRegistry(
            WorkspaceClient(),
            _required(settings.warehouse_id, "IDP_WAREHOUSE_ID"),
            catalog,
            schema,
            prefix,
            statement_deadline_seconds=settings.sql_statement_deadline_seconds,
        )
        repository = DatabricksBatchRepository(sql, f"{catalog}.{schema}.{prefix}")
    return UploadBatchService(
        repository,
        documents,
        settings.max_upload_batch_files,
        settings.max_upload_attempts,
        settings.upload_claim_seconds,
    )


def get_folder_import_service(request: Request) -> "FolderImportService":
    from idp_app.services.folder_import import FolderImportService

    existing = getattr(request.app.state, "folder_import_service", None)
    if isinstance(existing, FolderImportService):
        return existing
    settings = cast(Settings, request.app.state.settings)
    if not settings.folder_import_enabled:
        raise DocumentServiceError(
            "FOLDER_IMPORT_DISABLED", "Folder import is not configured for this app.", 404
        )
    service = build_folder_import_service(settings, get_upload_batch_service(request))
    request.app.state.folder_import_service = service
    return service


def build_folder_import_service(
    settings: Settings, uploads: "UploadBatchService", *, start_runs: bool = True
) -> "FolderImportService":
    """``start_runs=False`` builds the Job side, which only runs imports."""
    from idp_app.services.folder_import import (
        DatabricksImportJobRunner,
        DatabricksImportSource,
        FolderImportService,
        ImportSource,
        LocalImportSource,
        background_runner,
    )

    source: ImportSource
    if settings.mode is IdpMode.MOCK:
        source = LocalImportSource(settings.local_data_dir / "import_volume")
    else:
        source = DatabricksImportSource(
            WorkspaceClient(),
            _required(settings.catalog, "IDP_CATALOG"),
            _required(settings.project_schema, "IDP_PROJECT_SCHEMA"),
            _required(settings.import_volume_name, "IDP_IMPORT_VOLUME_NAME"),
        )

    def not_startable(batch_id: str, resume: bool) -> None:
        raise RuntimeError("This process runs imports; it does not start them")

    service = FolderImportService(source, uploads, not_startable, settings.import_concurrency)
    if start_runs:
        if settings.mode is IdpMode.MOCK:
            service.start_run = background_runner(service.run)
        else:
            job_id = settings.import_job_id
            if job_id is None:
                raise DocumentServiceError(
                    "FOLDER_IMPORT_DISABLED", "Folder import is not configured for this app.", 404
                )
            service.start_run = DatabricksImportJobRunner(WorkspaceClient(), job_id)
    return service


def _preparation(
    settings: Settings, documents: DocumentRegistry, runs: ParseRunRepository
) -> PreparationService | None:
    if not settings.auto_prepare_enabled:
        return None
    from idp_app.services.work_batches import DatabricksWorkRepository, SQLiteWorkRepository
    from idp_app.services.work_wakeup import wake_dispatcher

    repository: WorkRepository
    if isinstance(documents, DatabricksDocumentRegistry):
        repository = DatabricksWorkRepository(
            documents, f"{settings.catalog}.{settings.project_schema}.{settings.table_prefix}"
        )
    else:
        repository = SQLiteWorkRepository(settings.local_data_dir / "registry.sqlite3")
    job_id = settings.dispatch_job_id
    return PreparationService(
        settings, documents, runs, repository, (lambda: wake_dispatcher(job_id)) if job_id else None
    )


def _registration_callback(
    settings: Settings, documents: DocumentRegistry
) -> Callable[[DocumentRecord], object] | None:
    if not settings.auto_prepare_enabled:
        return None
    runs: ParseRunRepository
    if isinstance(documents, DatabricksDocumentRegistry):
        runs = DatabricksParseRunRepository(
            documents,
            _required(settings.catalog, "catalog"),
            _required(settings.project_schema, "schema"),
            _required(settings.table_prefix, "prefix"),
        )
    else:
        runs = SQLiteParseRunRepository(settings.local_data_dir / "registry.sqlite3")
    preparation = _preparation(settings, documents, runs)
    assert preparation is not None
    return lambda document: preparation.request(document.document_id, document.uploaded_by)


def get_document_chat_service(request: Request) -> "DocumentChatService":
    from idp_app.services.document_chat import ChatError, DocumentChatService

    existing = getattr(request.app.state, "document_chat_service", None)
    if isinstance(existing, DocumentChatService):
        return existing
    settings = cast(Settings, request.app.state.settings)
    if not settings.chat_enabled:
        raise ChatError("CHAT_DISABLED", "Document chat is not configured for this app.", 404)
    service = build_document_chat_service(settings, get_document_service(request))
    request.app.state.document_chat_service = service
    return service


def build_document_chat_service(
    settings: Settings, documents: DocumentService
) -> "DocumentChatService":
    from databricks.sdk.core import Config

    from idp_app.services.document_chat import (
        ChatClient,
        ChatRepository,
        DatabricksChatRepository,
        DocumentChatService,
        MockChatClient,
        ServingEndpointChatClient,
        SQLiteChatRepository,
    )

    registry = documents.registry
    client: ChatClient
    repository: ChatRepository
    if settings.mode is IdpMode.MOCK:
        client = MockChatClient()
        repository = SQLiteChatRepository(settings.local_data_dir / "registry.sqlite3")
    else:
        assert isinstance(registry, DatabricksDocumentRegistry)
        # Supervisor answers can take a minute when several tools run.
        client = ServingEndpointChatClient(
            WorkspaceClient(config=Config(http_timeout_seconds=170)),
            _required(settings.chat_endpoint, "chat"),
        )
        repository = DatabricksChatRepository(
            registry, f"{settings.catalog}.{settings.project_schema}.{settings.table_prefix}"
        )

    def file_names(document_ids: list[str]) -> dict[str, str]:
        return {key: record.file_name for key, record in registry.get_many(document_ids).items()}

    return DocumentChatService(client, repository, file_names)
