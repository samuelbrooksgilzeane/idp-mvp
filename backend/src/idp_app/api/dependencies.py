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
from idp_app.services.parse_jobs import DatabricksParseJobRunner, MockParseJobRunner
from idp_app.services.parse_runs import (
    DatabricksParseRunRepository,
    SQLiteParseRunRepository,
)
from idp_app.services.parsing import ParsingService


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
