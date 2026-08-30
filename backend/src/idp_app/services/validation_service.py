"""Orchestrates one deterministic validation attempt.

Deterministic validation is pure computation over already-persisted data, so unlike parsing and
extraction it needs no Databricks Job and completes synchronously.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from idp_app.services.document_models import (
    ValidationResultRecord,
    ValidationRunRecord,
)
from idp_app.services.document_registry import DocumentRegistry
from idp_app.services.documents import DocumentServiceError
from idp_app.services.extraction_runs import ExtractionRunRepository
from idp_app.services.parse_runs import ParseRunRepository
from idp_app.services.schema_registry import SchemaRepository
from idp_app.services.validation import (
    VALIDATOR_VERSION,
    Observation,
    ValidationContext,
    decide_document_status,
    run_validators,
)
from idp_app.services.validation_runs import ValidationRunRepository

VALIDATABLE_DOCUMENT_STATES = {
    "EXTRACTED",
    "VALIDATING",
    "VALIDATED_PASS",
    "REVIEW_REQUIRED",
}


class ValidationService:
    def __init__(
        self,
        documents: DocumentRegistry,
        parse_runs: ParseRunRepository,
        extraction_runs: ExtractionRunRepository,
        schemas: SchemaRepository,
        validation_runs: ValidationRunRepository,
    ) -> None:
        self._documents = documents
        self._parse_runs = parse_runs
        self._extraction_runs = extraction_runs
        self._schemas = schemas
        self._runs = validation_runs

    async def validate(
        self,
        document_id: str,
        requested_by: str,
        extraction_run_id: str | None = None,
    ) -> tuple[ValidationRunRecord, list[ValidationResultRecord]]:
        document = await run_in_threadpool(self._documents.get, document_id)
        if document is None:
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404)

        if extraction_run_id is None:
            extraction = await run_in_threadpool(
                self._extraction_runs.latest_successful, document_id
            )
        else:
            extraction = await run_in_threadpool(self._extraction_runs.get, extraction_run_id)
            if extraction is not None and extraction.document_id != document_id:
                extraction = None
        if extraction is None or extraction.status != "EXTRACTED":
            raise DocumentServiceError(
                "SUCCESSFUL_EXTRACTION_REQUIRED",
                "A successful extraction is required before validation.",
                409,
                document_id=document_id,
            )

        schema = await run_in_threadpool(
            self._schemas.get, extraction.schema_id, extraction.schema_version
        )
        if schema is None:
            raise DocumentServiceError(
                "SCHEMA_NOT_FOUND", "The extraction schema is no longer registered.", 404
            )

        fields = await run_in_threadpool(
            self._extraction_runs.list_fields, extraction.extraction_run_id
        )
        candidates = await run_in_threadpool(
            self._extraction_runs.list_candidates, extraction.extraction_run_id
        )
        parse = await run_in_threadpool(self._parse_runs.get, extraction.parse_run_id)
        latest_parse = await run_in_threadpool(self._parse_runs.latest_successful, document_id)
        registered = await run_in_threadpool(
            self._schemas.list, "PRODUCTION", document.use_case
        )
        versions = [item for item in registered if item.schema_id == extraction.schema_id]
        latest_version = max((item.schema_version for item in versions), default=None)
        # Every invoice the document states carries its own business identity, so a duplicate
        # is looked for against each of them rather than only the first.
        duplicates: list[str] = []
        for candidate in candidates:
            found = await run_in_threadpool(
                self._runs.find_business_duplicates,
                document_id,
                candidate.seller_name,
                candidate.invoice_number,
            )
            duplicates.extend(item for item in found if item not in duplicates)

        context = ValidationContext(
            document=document,
            run=extraction,
            schema=schema,
            fields=fields,
            parse=parse,
            registered_schema_hash=schema.schema_hash,
            latest_schema_version=latest_version,
            latest_parse_run_id=latest_parse.parse_run_id if latest_parse else None,
            duplicate_document_ids=tuple(duplicates),
        )

        started_at = datetime.now(UTC)
        observations = run_validators(context)
        document_status = decide_document_status(observations)
        validation_run_id = str(uuid4())
        completed_at = datetime.now(UTC)

        run = ValidationRunRecord(
            validation_run_id=validation_run_id,
            document_id=document_id,
            extraction_run_id=extraction.extraction_run_id,
            schema_id=extraction.schema_id,
            schema_version=extraction.schema_version,
            schema_hash=extraction.schema_hash,
            validator_version=VALIDATOR_VERSION,
            status="COMPLETED",
            document_status=document_status,
            requested_by=requested_by,
            started_at=started_at,
            completed_at=completed_at,
        )
        results = [
            _result(observation, run, extraction.extraction_run_id, document_id, completed_at)
            for observation in observations
        ]
        await run_in_threadpool(self._runs.save, run, results)

        if document.status in VALIDATABLE_DOCUMENT_STATES:
            await run_in_threadpool(
                self._documents.update_status,
                document_id,
                VALIDATABLE_DOCUMENT_STATES,
                document_status,
            )
        return run, results

    async def list_runs(self, document_id: str) -> list[ValidationRunRecord]:
        await self._require_document(document_id)
        return await run_in_threadpool(self._runs.list_for_document, document_id)

    async def latest(
        self, document_id: str
    ) -> tuple[ValidationRunRecord, list[ValidationResultRecord]]:
        await self._require_document(document_id)
        run = await run_in_threadpool(self._runs.latest, document_id)
        if run is None:
            raise DocumentServiceError(
                "VALIDATION_RUN_NOT_FOUND",
                "No validation has been run for this document.",
                404,
                document_id=document_id,
            )
        results = await run_in_threadpool(self._runs.list_results, run.validation_run_id)
        return run, results

    async def result(
        self, document_id: str, validation_run_id: str
    ) -> tuple[ValidationRunRecord, list[ValidationResultRecord]]:
        await self._require_document(document_id)
        run = await run_in_threadpool(self._runs.get, validation_run_id)
        if run is None or run.document_id != document_id:
            raise DocumentServiceError(
                "VALIDATION_RUN_NOT_FOUND",
                "Validation run not found for this document.",
                404,
                document_id=document_id,
            )
        results = await run_in_threadpool(self._runs.list_results, run.validation_run_id)
        return run, results

    async def _require_document(self, document_id: str) -> None:
        document = await run_in_threadpool(self._documents.get, document_id)
        if document is None:
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404)


def summarise(results: list[ValidationResultRecord]) -> dict[str, int]:
    """Counts by status and by severity for non-passing observations."""
    summary = {
        "total": len(results),
        "passed": 0,
        "failed": 0,
        "uncertain": 0,
        "skipped": 0,
        "blocking": 0,
        "warning": 0,
        "info": 0,
    }
    for result in results:
        summary[
            {"PASS": "passed", "FAIL": "failed", "UNCERTAIN": "uncertain", "SKIPPED": "skipped"}[
                result.status
            ]
        ] += 1
        if result.status in {"FAIL", "UNCERTAIN"}:
            summary[result.severity.lower()] += 1
    return summary


def _result(
    observation: Observation,
    run: ValidationRunRecord,
    extraction_run_id: str,
    document_id: str,
    created_at: datetime,
) -> ValidationResultRecord:
    return ValidationResultRecord(
        validation_run_id=run.validation_run_id,
        extraction_run_id=extraction_run_id,
        document_id=document_id,
        rule_id=observation.rule_id,
        field_path=observation.field_path,
        validator_type=observation.validator_type,
        severity=observation.severity,
        status=observation.status,
        message=observation.message,
        actual_value=observation.actual_value,
        expected_value=observation.expected_value,
        suggested_value=None,
        evidence=observation.evidence,
        validator_version=VALIDATOR_VERSION,
        prompt_hash=None,
        created_at=created_at,
    )
