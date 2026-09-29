"""Resolve and pin trusted extraction metadata without reading retained parse bodies."""

from dataclasses import dataclass

from idp_app.services.bulk_ids import id_chunks
from idp_app.services.document_models import DocumentRecord, ParseRunReference
from idp_app.services.document_registry import DocumentRegistry
from idp_app.services.documents import DocumentServiceError
from idp_app.services.job_batches import BatchFailure
from idp_app.services.parse_runs import ParseRunRepository
from idp_app.services.schema_models import SchemaManifest, SchemaRecord
from idp_app.services.schema_registry import SchemaRepository

ELIGIBLE_DOCUMENT_STATES = {
    "PARSED",
    "EXTRACTED",
    "EXTRACT_FAILED",
    "VALIDATED_PASS",
    "REVIEW_REQUIRED",
}


@dataclass(frozen=True)
class ExtractionInputs:
    document: DocumentRecord
    parse: ParseRunReference
    schema: SchemaRecord


def published_schema(schemas: SchemaRepository, schema_id: str, version: int) -> SchemaRecord:
    schema = schemas.get(schema_id, version)
    if schema is None:
        raise DocumentServiceError("SCHEMA_NOT_FOUND", "Extraction schema not found.", 404)
    if schema.status not in {"PRODUCTION", "PUBLISHED"}:
        raise DocumentServiceError(
            "SCHEMA_NOT_PRODUCTION",
            "Only a published (or production) schema can be extracted.",
            409,
        )
    return schema


def resolve_extraction_inputs(
    documents: DocumentRegistry,
    parses: ParseRunRepository,
    schema: SchemaRecord,
    document_ids: list[str],
) -> tuple[list[ExtractionInputs], list[BatchFailure]]:
    resolved = []
    failures = []
    for chunk in id_chunks(document_ids):
        records = documents.get_many(chunk)
        references = parses.latest_successful_references(chunk)
        for identity in chunk:
            document, parse = records.get(identity), references.get(identity)
            if document is None or document.status == "DELETED":
                code, message = "DOCUMENT_NOT_FOUND", "Document not found."
            elif parse is None:
                code, message = (
                    "SUCCESSFUL_PARSE_REQUIRED",
                    "A successful parse is required before extraction.",
                )
            elif parse.content_sha256 != document.content_sha256:
                code, message = (
                    "PARSE_SOURCE_MISMATCH",
                    "The successful parse belongs to a different source version.",
                )
            elif document.status not in ELIGIBLE_DOCUMENT_STATES:
                code, message = (
                    "DOCUMENT_NOT_EXTRACTABLE",
                    f"Document cannot be extracted from status {document.status}.",
                )
            else:
                resolved.append(ExtractionInputs(document, parse, schema))
                continue
            failures.append(BatchFailure(document_id=identity, code=code, message=message))
    return resolved, failures


def verify_schema_content(schema: SchemaRecord) -> None:
    manifest = SchemaManifest.model_validate({
        "schema_id": schema.schema_id,
        "schema_version": schema.schema_version,
        "display_name": schema.display_name,
        "use_case": schema.use_case,
        "status": schema.status,
        "description": schema.description,
        "instructions": schema.instructions,
        "ai_extract_schema": schema.ai_extract_schema,
        "field_policies": schema.field_policies,
        "document_rules": schema.document_rules,
    })
    if manifest.schema_hash != schema.schema_hash:
        raise ValueError("Pinned template hash does not match its content")
