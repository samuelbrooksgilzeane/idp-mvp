"""Public display configuration only; the chat app authenticates each viewer itself."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from idp_app.api.dependencies import get_authenticated_user

router = APIRouter(tags=["configuration"])


@router.get("/app-config")
def app_config(
    request: Request, user: Annotated[str, Depends(get_authenticated_user)]
) -> dict[str, Any]:
    del user
    settings = request.app.state.settings
    return {
        "project_name": settings.app_name,
        "chat_app_url": settings.chat_app_url,
        "chat_enabled": settings.chat_enabled,
    }
