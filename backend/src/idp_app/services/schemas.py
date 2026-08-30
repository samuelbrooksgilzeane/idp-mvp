import json
import re
from pathlib import Path
from typing import Literal

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from idp_app.services.documents import DocumentServiceError
from idp_app.services.schema_models import (
    MAX_SCHEMA_DEPTH,
    MAX_SCHEMA_LEAVES,
    ExtractField,
    FieldPolicy,
    SchemaManifest,
    SchemaRecord,
    schema_leaves,
)
from idp_app.services.schema_registry import SchemaNotDraftError, SchemaRepository

SCHEMA_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


class SchemaService:
    def __init__(self, repository: SchemaRepository) -> None:
        self._repository = repository

    # -- Read paths -------------------------------------------------------------------

    async def list_schemas(
        self,
        status: str = "PRODUCTION",
        use_case: str | None = None,
    ) -> list[SchemaRecord]:
        if status != "PRODUCTION":
            raise DocumentServiceError(
                "SCHEMA_STATUS_UNAVAILABLE",
                "Only production extraction schemas are available.",
                422,
            )
        return await run_in_threadpool(self._repository.list, status, use_case)

    async def list_all_schemas(self, use_case: str | None = None) -> list[SchemaRecord]:
        """Every schema version in every lifecycle status: governed, published, or draft.

        Backs the schema editor's list view, which shows a user's own drafts alongside every
        published and seeded-template schema.
        """
        return await run_in_threadpool(self._repository.list_all, use_case)

    async def get_schema(self, schema_id: str, schema_version: int) -> SchemaRecord:
        schema = await run_in_threadpool(self._repository.get, schema_id, schema_version)
        if schema is None:
            raise DocumentServiceError(
                "SCHEMA_NOT_FOUND",
                "The requested extraction schema version was not found.",
                404,
            )
        return schema

    async def get_production_schema(self, schema_id: str, schema_version: int) -> SchemaRecord:
        """The historical, PRODUCTION-only read used before the schema editor existed."""
        schema = await self.get_schema(schema_id, schema_version)
        if schema.status not in ("PRODUCTION", "PUBLISHED"):
            raise DocumentServiceError(
                "SCHEMA_NOT_FOUND",
                "The requested production extraction schema was not found.",
                404,
            )
        return schema

    # -- Write paths (section 2: the user-editable schema registry) -------------------

    async def create_schema(
        self,
        display_name: str,
        description: str | None,
        root_mode: Literal["SINGLE_RECORD", "REPEATED_RECORDS"],
        created_by: str,
        use_case: str = "generic",
    ) -> SchemaRecord:
        """Start a new DRAFT schema (version 1), seeded with one starter field so it is
        immediately valid. `root_mode` answers "Can one document contain multiple records of
        this type?": REPEATED_RECORDS wraps the starter field in a top-level array, SINGLE_
        RECORD leaves it as a top-level scalar, matching a flat form.
        """
        schema_id = await self._unique_schema_id(display_name)
        starter_field = ExtractField(type="string", description="Describe what this field holds.")
        if root_mode == "REPEATED_RECORDS":
            ai_extract_schema = {
                "records": ExtractField(
                    type="array",
                    description="Each record of this type stated in the document.",
                    items=ExtractField(
                        type="object",
                        description="One record.",
                        properties={"field_1": starter_field},
                    ),
                )
            }
        else:
            ai_extract_schema = {"field_1": starter_field}

        manifest = self._build_manifest(
            schema_id=schema_id,
            schema_version=1,
            display_name=display_name,
            description=description,
            use_case=use_case,
            instructions="Extract only values explicitly stated in the source document.",
            ai_extract_schema=ai_extract_schema,
            field_policies=None,
        )
        return await self._save_draft(manifest, created_by)

    async def update_draft(
        self,
        schema_id: str,
        schema_version: int,
        *,
        display_name: str | None = None,
        description: str | None = None,
        instructions: str | None = None,
        use_case: str | None = None,
        ai_extract_schema: dict[str, ExtractField],
        field_policies: dict[str, FieldPolicy] | None,
        updated_by: str,
    ) -> SchemaRecord:
        current = await self.get_schema(schema_id, schema_version)
        if not current.is_editable:
            raise DocumentServiceError(
                "SCHEMA_NOT_DRAFT",
                "Only a draft schema version can be edited.",
                409,
            )
        manifest = self._build_manifest(
            schema_id=schema_id,
            schema_version=schema_version,
            display_name=display_name or current.display_name,
            description=description if description is not None else current.description,
            use_case=use_case or current.use_case,
            instructions=instructions or current.instructions,
            ai_extract_schema=ai_extract_schema,
            field_policies=field_policies,
        )
        return await self._save_draft(manifest, updated_by)

    async def validate_schema(
        self, ai_extract_schema: dict[str, ExtractField]
    ) -> "SchemaValidationReport":
        """Server-side structural validation without persisting anything: depth, field-count,
        and naming errors are returned rather than raised, so the editor can show them inline.
        """
        errors: list[str] = []
        leaf_count = 0
        depth = 0
        try:
            leaves = schema_leaves(ai_extract_schema)
            leaf_count = len(leaves)
            depth = _max_depth(ai_extract_schema)
        except ValueError as error:
            errors.append(str(error))
        if leaf_count > MAX_SCHEMA_LEAVES:
            errors.append(
                f"Schema declares {leaf_count} fields, exceeding the limit of {MAX_SCHEMA_LEAVES}."
            )
        invalid_names = [
            name for name in ai_extract_schema if not SCHEMA_ID_PATTERN.fullmatch(name)
        ]
        if invalid_names:
            errors.append(f"Invalid field name: {invalid_names[0]}")
        return SchemaValidationReport(
            valid=not errors,
            depth=depth,
            max_depth=MAX_SCHEMA_DEPTH,
            leaf_count=leaf_count,
            max_leaves=MAX_SCHEMA_LEAVES,
            errors=errors,
        )

    async def publish_schema(self, schema_id: str, schema_version: int) -> SchemaRecord:
        current = await self.get_schema(schema_id, schema_version)
        if not current.is_editable:
            raise DocumentServiceError(
                "SCHEMA_NOT_DRAFT",
                "Only a draft schema version can be published.",
                409,
            )
        try:
            return await run_in_threadpool(
                self._repository.publish, schema_id, schema_version
            )
        except SchemaNotDraftError as error:
            raise DocumentServiceError("SCHEMA_NOT_DRAFT", str(error), 409) from error

    async def clone_schema(
        self,
        source_schema_id: str,
        source_schema_version: int,
        new_display_name: str,
        created_by: str,
        new_schema_id: str | None = None,
    ) -> SchemaRecord:
        """Clone a published (or seed-template) schema into a new editable DRAFT.

        With `new_schema_id` omitted, the clone becomes the next DRAFT version of the *same*
        schema_id -- the "edit a published schema" path, since a published version is
        otherwise immutable. With `new_schema_id` given, it starts an unrelated schema seeded
        from the source's shape, e.g. cloning the `invoice` seed template as a starting point
        for a new custom schema.
        """
        source = await self.get_schema(source_schema_id, source_schema_version)
        if new_schema_id is None:
            schema_id = source_schema_id
            next_version = await run_in_threadpool(
                self._repository.latest_version, schema_id
            )
            schema_version = next_version + 1
        else:
            schema_id = new_schema_id
            schema_version = 1

        manifest = self._build_manifest(
            schema_id=schema_id,
            schema_version=schema_version,
            display_name=new_display_name,
            description=source.description,
            use_case=source.use_case,
            instructions=source.instructions,
            ai_extract_schema=source.ai_extract_schema,
            field_policies=None,
        )
        return await self._save_draft(manifest, created_by)

    # -- Internals ----------------------------------------------------------------------

    async def _unique_schema_id(self, display_name: str) -> str:
        base = re.sub(r"[^a-z0-9_]+", "_", display_name.strip().lower()).strip("_") or "schema"
        if not base[0].isalpha():
            base = f"schema_{base}"
        base = base[:80]
        candidate = base
        suffix = 2
        while await run_in_threadpool(self._repository.latest_version, candidate):
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    def _build_manifest(
        self,
        *,
        schema_id: str,
        schema_version: int,
        display_name: str,
        description: str | None,
        use_case: str,
        instructions: str,
        ai_extract_schema: dict[str, ExtractField],
        field_policies: dict[str, FieldPolicy] | None,
    ) -> SchemaManifest:
        resolved_policies = field_policies or {
            path: FieldPolicy() for path, _ in schema_leaves(ai_extract_schema)
        }
        try:
            return SchemaManifest(
                schema_id=schema_id,
                schema_version=schema_version,
                display_name=display_name,
                description=description,
                use_case=use_case,
                status="DRAFT",
                instructions=instructions,
                ai_extract_schema=ai_extract_schema,
                field_policies=resolved_policies,
                document_rules=[],
            )
        except (ValueError, ValidationError) as error:
            raise DocumentServiceError("SCHEMA_INVALID", str(error), 422) from error

    async def _save_draft(self, manifest: SchemaManifest, created_by: str) -> SchemaRecord:
        try:
            return await run_in_threadpool(
                self._repository.save_draft, manifest, created_by
            )
        except SchemaNotDraftError as error:
            raise DocumentServiceError("SCHEMA_NOT_DRAFT", str(error), 409) from error


class SchemaValidationReport:
    def __init__(
        self,
        *,
        valid: bool,
        depth: int,
        max_depth: int,
        leaf_count: int,
        max_leaves: int,
        errors: list[str],
    ) -> None:
        self.valid = valid
        self.depth = depth
        self.max_depth = max_depth
        self.leaf_count = leaf_count
        self.max_leaves = max_leaves
        self.errors = errors


def _max_depth(schema: dict[str, ExtractField], depth: int = 1) -> int:
    deepest = depth
    for field in schema.values():
        if field.type == "array" and field.items is not None:
            child = field.items
            deepest = max(
                deepest,
                _max_depth(child.properties, depth + 1) if child.properties else depth + 1,
            )
        elif field.type == "object" and field.properties is not None:
            deepest = max(deepest, _max_depth(field.properties, depth + 1))
    return deepest


def load_manifest(path: Path) -> SchemaManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return SchemaManifest.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise ValueError(f"Invalid schema manifest: {path.name}") from error


def manifest_directory() -> Path:
    return Path(__file__).resolve().parents[4] / "schemas"


def load_source_manifests(directory: Path | None = None) -> list[SchemaManifest]:
    root = directory or manifest_directory()
    manifests = [load_manifest(path) for path in sorted(root.glob("*.json"))]
    if not manifests:
        raise RuntimeError("No source-controlled extraction schema manifests were found")
    return manifests
