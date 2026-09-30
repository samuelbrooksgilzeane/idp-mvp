from collections.abc import MutableMapping
from pathlib import Path
from time import perf_counter
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from idp_app.api.router import api_router
from idp_app.core.config import Settings
from idp_app.core.performance import (
    begin_request_metrics,
    current_request_metrics,
    reset_request_metrics,
)
from idp_app.services.documents import DocumentServiceError


class SinglePageStaticFiles(StaticFiles):
    """Serve the built client, falling back to its entry point for client-side routes.

    The browser may deep-link or refresh on a route such as `/documents/{id}`, which is not a
    file on disk. Extension-less paths therefore fall back to `index.html` so the client router
    can resolve them, while a missing asset still returns a genuine 404 rather than HTML.
    """

    async def get_response(self, path: str, scope: MutableMapping[str, Any]) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as error:
            # A request for an extension-less path is a client route, not a missing file.
            if error.status_code == 404 and "." not in Path(path).name:
                response = await super().get_response("index.html", scope)
            else:
                raise
        # Content-hashed build assets never change; the HTML entry point names the current ones
        # and must be revalidated, otherwise browsers keep loading the previous release's code.
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application instance and validate its trusted configuration."""
    resolved_settings = settings or Settings()
    app = FastAPI(title=resolved_settings.app_name, version="0.1.0")
    app.state.settings = resolved_settings

    @app.middleware("http")
    async def add_server_timing(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Expose safe aggregate request/warehouse timings for browser and API diagnostics."""
        started_at = perf_counter()
        token = begin_request_metrics()
        try:
            response = await call_next(request)
            metrics = current_request_metrics()
            total_ms = (perf_counter() - started_at) * 1000
            timings = [f"app;dur={total_ms:.1f}"]
            if metrics is not None and metrics.sql_statement_count:
                statement_count = metrics.sql_statement_count
                timings.append(
                    f'sql;dur={metrics.sql_duration_ms:.1f};desc="{statement_count} statements"'
                )
            response.headers["Server-Timing"] = ", ".join(timings)
            existing = response.headers.get("Content-Security-Policy", "")
            directives = [
                item.strip()
                for item in existing.split(";")
                if item.strip() and not item.strip().startswith("frame-src ")
            ]
            directives.append("frame-src 'none'")
            response.headers["Content-Security-Policy"] = "; ".join(directives)
            return response
        finally:
            reset_request_metrics(token)

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
