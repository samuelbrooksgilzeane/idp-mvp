from typing import cast

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
from idp_app.services.extraction import ExtractionService
from idp_app.services.extraction_jobs import (
    DatabricksExtractionJobRunner,
    MockExtractionJobRunner,
)
from idp_app.services.extraction_runs import (
    DatabricksExtractionRunRepository,
    SQLiteExtractionRunRepository,
)
from idp_app.services.parse_jobs import DatabricksParseJobRunner, MockParseJobRunner
from idp_app.services.parse_runs import (
    DatabricksParseRunRepository,
    SQLiteParseRunRepository,
)
from idp_app.services.parsing import ParsingService
from idp_app.services.reporting import (
    DatabricksReportingRepository,
    ReportingService,
    SQLiteReportingRepository,
)
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
        return DocumentService(storage, registry, settings.max_upload_bytes)

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
    )
    return DocumentService(
        databricks_storage,
        databricks_registry,
        settings.max_upload_bytes,
    )


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
        return ParsingService(settings, mock_documents, mock_parse_runs, mock_jobs)

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
    database_path = settings.local_data_dir / "registry.sqlite3"
    if settings.mode is IdpMode.MOCK:
        return ViewerService(
            SQLiteDocumentRegistry(database_path),
            SQLiteParseRunRepository(database_path),
            LocalPageImageStorage(
                settings.local_data_dir / "artifacts_volume" / "page_images"
            ),
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
        repository = SQLiteSchemaRepository(
            settings.local_data_dir / "registry.sqlite3"
        )
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
        mock_jobs = MockExtractionJobRunner(mock_runs, mock_documents)
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
        client, warehouse_id, catalog, project_schema, table_prefix
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
        client, warehouse_id, catalog, project_schema, table_prefix
    )
    return ValidationService(
        documents,
        DatabricksParseRunRepository(documents, catalog, project_schema, table_prefix),
        DatabricksExtractionRunRepository(documents, catalog, project_schema, table_prefix),
        DatabricksSchemaRepository(documents, catalog, project_schema, table_prefix),
        DatabricksValidationRunRepository(documents, catalog, project_schema, table_prefix),
    )


def get_reporting_service(request: Request) -> ReportingService:
    existing = getattr(request.app.state, "reporting_service", None)
    if isinstance(existing, ReportingService):
        return existing
    settings = cast(Settings, request.app.state.settings)
    service = build_reporting_service(settings)
    request.app.state.reporting_service = service
    return service


def build_reporting_service(settings: Settings) -> ReportingService:
    database_path = settings.local_data_dir / "registry.sqlite3"
    if settings.mode is IdpMode.MOCK:
        # Reporting can be the first feature opened in local mode. Initialise the
        # projection tables before its read-only query is issued.
        SQLiteDocumentRegistry(database_path)
        SQLiteExtractionRunRepository(database_path)
        SQLiteValidationRunRepository(database_path)
        return ReportingService(SQLiteReportingRepository(database_path))

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
        client, warehouse_id, catalog, project_schema, table_prefix
    )
    return ReportingService(
        DatabricksReportingRepository(
            sql_client, catalog, project_schema, table_prefix
        )
    )
