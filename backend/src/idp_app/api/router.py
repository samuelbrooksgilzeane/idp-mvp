from fastapi import APIRouter, Request

from idp_app.api.documents import documents_router
from idp_app.api.models import HealthResponse
from idp_app.core.config import Settings
from idp_app.services.health import build_health_response

api_router = APIRouter()
api_router.include_router(documents_router)


@api_router.get("/health", response_model=HealthResponse, tags=["system"])
def health(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    return build_health_response(settings)
