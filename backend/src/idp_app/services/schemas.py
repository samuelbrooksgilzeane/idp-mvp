import json
from pathlib import Path

from pydantic import ValidationError

from idp_app.services.documents import DocumentServiceError
from idp_app.services.schema_models import SchemaManifest, SchemaRecord
from idp_app.services.schema_registry import SchemaRepository


class SchemaService:
    def __init__(self, repository: SchemaRepository) -> None:
        self._repository = repository

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
        return self._repository.list(status, use_case)

    async def get_schema(self, schema_id: str, schema_version: int) -> SchemaRecord:
        schema = self._repository.get(schema_id, schema_version)
        if schema is None or schema.status != "PRODUCTION":
            raise DocumentServiceError(
                "SCHEMA_NOT_FOUND",
                "The requested production extraction schema was not found.",
                404,
            )
        return schema


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
