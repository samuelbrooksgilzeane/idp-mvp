from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from idp_app.api.router import api_router
from idp_app.core.config import Settings
from idp_app.services.documents import DocumentServiceError


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application instance and validate its trusted configuration."""
    resolved_settings = settings or Settings()
    app = FastAPI(title=resolved_settings.app_name, version="0.1.0")
    app.state.settings = resolved_settings

    @app.exception_handler(DocumentServiceError)
    async def document_service_error_handler(
        request: Request, error: DocumentServiceError
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "document_id": error.document_id,
                }
            },
        )

    app.include_router(api_router, prefix="/api")

    frontend_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app
