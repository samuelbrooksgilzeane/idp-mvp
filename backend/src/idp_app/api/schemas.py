from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from idp_app.api.dependencies import get_authenticated_user, get_schema_service
from idp_app.api.models import (
    CloneSchemaRequest,
    CreateSchemaRequest,
    ErrorResponse,
    SchemaDetailResponse,
    SchemaFieldResponse,
    SchemaRuleResponse,
    SchemaSummaryResponse,
    SchemaValidationResponse,
    UpdateSchemaDraftRequest,
    ValidateSchemaRequest,
)
from idp_app.services.schema_models import SchemaRecord, schema_leaves
from idp_app.services.schemas import SchemaService

schemas_router = APIRouter(prefix="/schemas", tags=["schemas"])


@schemas_router.get(
    "",
    response_model=list[SchemaSummaryResponse],
    responses={422: {"model": ErrorResponse}},
)
async def list_schemas(
    service: Annotated[SchemaService, Depends(get_schema_service)],
    status: Annotated[
        Literal["PRODUCTION", "ALL"],
        Query(),
    ] = "PRODUCTION",
    use_case: Annotated[
        str | None,
        Query(pattern=r"^[a-z][a-z0-9_]{0,99}$"),
    ] = None,
) -> list[SchemaSummaryResponse]:
    """List extraction schemas. `status=PRODUCTION` (the default) preserves the historical,
    governed-only contract; `status=ALL` additionally lists every draft, published and
    retired custom schema version, for the schema editor's list view.
    """
    if status == "ALL":
        schemas = await service.list_all_schemas(use_case=use_case)
    else:
        schemas = await service.list_schemas(status=status, use_case=use_case)
    return [_summary(schema) for schema in schemas]


@schemas_router.post(
    "",
    response_model=SchemaDetailResponse,
    status_code=201,
    responses={422: {"model": ErrorResponse}},
)
async def create_schema(
    body: CreateSchemaRequest,
    service: Annotated[SchemaService, Depends(get_schema_service)],
    created_by: Annotated[str, Depends(get_authenticated_user)],
) -> SchemaDetailResponse:
    schema = await service.create_schema(
        display_name=body.display_name,
        description=body.description,
        root_mode=body.root_mode,
        use_case=body.use_case,
        created_by=created_by,
    )
    return _detail(schema)


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


@schemas_router.put(
    "/{schema_id}/draft",
    response_model=SchemaDetailResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def update_schema_draft(
    schema_id: str,
    schema_version: Annotated[int, Query(ge=1)],
    body: UpdateSchemaDraftRequest,
    service: Annotated[SchemaService, Depends(get_schema_service)],
    updated_by: Annotated[str, Depends(get_authenticated_user)],
) -> SchemaDetailResponse:
    schema = await service.update_draft(
        schema_id,
        schema_version,
        display_name=body.display_name,
        description=body.description,
        instructions=body.instructions,
        use_case=body.use_case,
        ai_extract_schema=body.ai_extract_schema,
        field_policies=None,
        updated_by=updated_by,
    )
    return _detail(schema)


@schemas_router.post(
    "/{schema_id}/validate",
    response_model=SchemaValidationResponse,
)
async def validate_schema(
    schema_id: str,
    body: ValidateSchemaRequest,
    service: Annotated[SchemaService, Depends(get_schema_service)],
) -> SchemaValidationResponse:
    report = await service.validate_schema(body.ai_extract_schema)
    return SchemaValidationResponse(
        valid=report.valid,
        depth=report.depth,
        max_depth=report.max_depth,
        leaf_count=report.leaf_count,
        max_leaves=report.max_leaves,
        errors=report.errors,
    )


@schemas_router.post(
    "/{schema_id}/publish",
    response_model=SchemaDetailResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def publish_schema(
    schema_id: str,
    schema_version: Annotated[int, Query(ge=1)],
    service: Annotated[SchemaService, Depends(get_schema_service)],
) -> SchemaDetailResponse:
    schema = await service.publish_schema(schema_id, schema_version)
    return _detail(schema)


@schemas_router.post(
    "/{schema_id}/clone",
    response_model=SchemaDetailResponse,
    status_code=201,
    responses={404: {"model": ErrorResponse}},
)
async def clone_schema(
    schema_id: str,
    schema_version: Annotated[int, Query(ge=1)],
    body: CloneSchemaRequest,
    service: Annotated[SchemaService, Depends(get_schema_service)],
    created_by: Annotated[str, Depends(get_authenticated_user)],
) -> SchemaDetailResponse:
    schema = await service.clone_schema(
        schema_id,
        schema_version,
        new_display_name=body.new_display_name,
        created_by=created_by,
        new_schema_id=body.new_schema_id,
    )
    return _detail(schema)


def _summary(schema: SchemaRecord) -> SchemaSummaryResponse:
    return SchemaSummaryResponse(
        schema_id=schema.schema_id,
        schema_version=schema.schema_version,
        display_name=schema.display_name,
        description=schema.description,
        use_case=schema.use_case,
        schema_hash=schema.schema_hash,
        status=schema.status,  # type: ignore[arg-type]
        root_mode=schema.root_mode,
        is_editable=schema.is_editable,
        created_by=schema.created_by,
        created_at=schema.created_at,
        published_at=schema.published_at,
    )


def _detail(schema: SchemaRecord) -> SchemaDetailResponse:
    fields = []
    for field_path, definition in schema_leaves(schema.ai_extract_schema):
        policy = schema.field_policies.get(field_path)
        fields.append(
            SchemaFieldResponse(
                field_path=field_path,
                label=field_path.replace("_", " ").title(),
                field_type=definition.type,
                description=definition.description,
                required=policy.required if policy else False,
                citation_required=policy.citation_required if policy else False,
                confidence_threshold=policy.confidence_threshold if policy else 0.0,
                risk_tier=policy.risk_tier if policy else "low",
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
        schema_tree=schema.ai_extract_schema,
    )
