from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from idp_app.api.dependencies import get_schema_service
from idp_app.api.models import (
    ErrorResponse,
    SchemaDetailResponse,
    SchemaFieldResponse,
    SchemaRuleResponse,
    SchemaSummaryResponse,
)
from idp_app.services.schema_models import SchemaRecord
from idp_app.services.schemas import SchemaService

schemas_router = APIRouter(prefix="/schemas", tags=["schemas"])


@schemas_router.get(
    "",
    response_model=list[SchemaSummaryResponse],
    responses={422: {"model": ErrorResponse}},
)
async def list_schemas(
    service: Annotated[SchemaService, Depends(get_schema_service)],
    status: Annotated[Literal["PRODUCTION"], Query()] = "PRODUCTION",
    use_case: Annotated[
        str | None,
        Query(pattern=r"^[a-z][a-z0-9_]{0,99}$"),
    ] = None,
) -> list[SchemaSummaryResponse]:
    return [
        _summary(schema)
        for schema in await service.list_schemas(status=status, use_case=use_case)
    ]


@schemas_router.get(
    "/{schema_id}/versions/{schema_version}",
    response_model=SchemaDetailResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_schema(
    schema_id: str,
    schema_version: int,
    service: Annotated[SchemaService, Depends(get_schema_service)],
) -> SchemaDetailResponse:
    return _detail(await service.get_schema(schema_id, schema_version))


def _summary(schema: SchemaRecord) -> SchemaSummaryResponse:
    return SchemaSummaryResponse(
        schema_id=schema.schema_id,
        schema_version=schema.schema_version,
        display_name=schema.display_name,
        use_case=schema.use_case,
        schema_hash=schema.schema_hash,
        status="PRODUCTION",
    )


def _detail(schema: SchemaRecord) -> SchemaDetailResponse:
    fields = []
    for field_path, definition in schema.ai_extract_schema.items():
        policy = schema.field_policies[field_path]
        fields.append(
            SchemaFieldResponse(
                field_path=field_path,
                label=field_path.replace("_", " ").title(),
                field_type=definition.type,
                description=definition.description,
                required=policy.required,
                citation_required=policy.citation_required,
                confidence_threshold=policy.confidence_threshold,
                risk_tier=policy.risk_tier,
            )
        )
    return SchemaDetailResponse(
        **_summary(schema).model_dump(),
        instructions=schema.instructions,
        fields=fields,
        document_rules=[
            SchemaRuleResponse.model_validate(rule, from_attributes=True)
            for rule in schema.document_rules
        ],
    )
