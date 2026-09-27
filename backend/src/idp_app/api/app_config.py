"""Public display configuration only; Genie authenticates each viewer directly."""

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
        "project_name": settings.genie_project_name or settings.app_name,
        "genie": {
            "enabled": settings.genie_enabled,
            "space_id": settings.genie_space_id if settings.genie_enabled else None,
            "embed_url": settings.genie_embed_url if settings.genie_enabled else None,
            "open_url": (
                f"{settings.genie_workspace_origin}/genie/rooms/{settings.genie_space_id}"
                if settings.genie_enabled
                else None
            ),
            "coverage": (
                "Answers use the sources configured for this project in Databricks. "
                "Project maintainers manage tables, volumes and search there. "
                "App document selections do not filter Genie."
            ),
        },
    }
