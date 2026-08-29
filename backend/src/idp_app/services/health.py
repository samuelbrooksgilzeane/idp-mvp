from idp_app.api.models import ConfigurationPresence, HealthResponse
from idp_app.core.config import Settings


def build_health_response(settings: Settings) -> HealthResponse:
    return HealthResponse(
        status="ok",
        mode=settings.mode,
        application_name=settings.app_name,
        configuration=ConfigurationPresence(
            catalog=settings.catalog is not None,
            project_schema=settings.project_schema is not None,
            table_prefix=settings.table_prefix is not None,
            source_volume_name=settings.source_volume_name is not None,
            artifacts_volume_name=settings.artifacts_volume_name is not None,
            warehouse_id=settings.warehouse_id is not None,
            parse_job_id=settings.parse_job_id is not None,
            extraction_job_id=settings.extraction_job_id is not None,
            validation_endpoint=settings.validation_endpoint is not None,
        ),
    )
