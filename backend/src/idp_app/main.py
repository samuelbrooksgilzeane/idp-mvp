from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from idp_app.api.router import api_router
from idp_app.core.config import Settings
from idp_app.services.documents import DocumentServiceError


class SinglePageStaticFiles(StaticFiles):
    """Serve the built client, falling back to its entry point for client-side routes.

    The browser may deep-link or refresh on a route such as `/documents/{id}`, which is not a
    file on disk. Extension-less paths therefore fall back to `index.html` so the client router
    can resolve them, while a missing asset still returns a genuine 404 rather than HTML.
    """

    async def get_response(self, path: str, scope: MutableMapping[str, Any]) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            # A request for an extension-less path is a client route, not a missing file.
            if error.status_code == 404 and "." not in Path(path).name:
                return await super().get_response("index.html", scope)
            raise


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
        app.mount(
            "/",
            SinglePageStaticFiles(directory=frontend_dist, html=True),
            name="frontend",
        )

    return app
