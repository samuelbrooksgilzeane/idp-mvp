from __future__ import annotations

import itertools
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Any, Protocol, cast

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs

from idp_app.services.document_models import DocumentRecord, ExtractionRunRecord, ParseRunRecord
from idp_app.services.document_registry import DocumentRegistry
from idp_app.services.extraction_result import (
    build_invoice_candidate,
    build_invoice_line_candidates,
    flatten_result,
)
from idp_app.services.extraction_runs import ExtractionRunRepository
from idp_app.services.schema_models import ExtractField, SchemaRecord


@dataclass(frozen=True)
class ExtractionJobRequest:
    run: ExtractionRunRecord
    document: DocumentRecord
    parse_run: ParseRunRecord
    schema: SchemaRecord


class ExtractionJobState(str, Enum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ExtractionJobPoll:
    state: ExtractionJobState
    message: str | None = None


class ExtractionJobRunner(Protocol):
    def trigger(self, request: ExtractionJobRequest) -> int: ...

    def poll(self, job_run_id: int) -> ExtractionJobPoll: ...


class MockExtractionJobRunner:
    def __init__(
        self,
        runs: ExtractionRunRepository,
        documents: DocumentRegistry,
        *,
        delay_seconds: float = 0.15,
    ) -> None:
        self._runs = runs
        self._documents = documents
        self._delay_seconds = delay_seconds
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="idp-extractor")
        self._ids = itertools.count(10_001)
        self._futures: dict[int, Future[None]] = {}
        self._lock = Lock()

    def trigger(self, request: ExtractionJobRequest) -> int:
        job_run_id = next(self._ids)
        future = self._executor.submit(self._execute, request)
        with self._lock:
            self._futures[job_run_id] = future
        return job_run_id

    def poll(self, job_run_id: int) -> ExtractionJobPoll:
        with self._lock:
            future = self._futures.get(job_run_id)
        if future is None:
            return ExtractionJobPoll(
                ExtractionJobState.FAILED, "Mock extraction job was not found."
            )
        if not future.done():
            return ExtractionJobPoll(ExtractionJobState.RUNNING)
        if future.exception() is not None:
            return ExtractionJobPoll(ExtractionJobState.FAILED, "Mock extraction job failed.")
        return ExtractionJobPoll(ExtractionJobState.SUCCEEDED)

    def _execute(self, request: ExtractionJobRequest) -> None:
        if self._delay_seconds:
            time.sleep(self._delay_seconds)
        try:
            ai_result = _mock_ai_extract(request.parse_run, request.schema)
            self._runs.retain_raw(request.run.extraction_run_id, ai_result)
            error_message = ai_result.get("error_message")
            if isinstance(error_message, str) and error_message:
                raise RuntimeError(error_message)
            fields = flatten_result(request.run, request.schema, ai_result)
            candidate = build_invoice_candidate(request.run, request.document, fields)
            lines = build_invoice_line_candidates(request.run, fields)
            self._runs.complete(request.run.extraction_run_id, fields, candidate, lines)
            self._documents.update_status(request.document.document_id, {"EXTRACTING"}, "EXTRACTED")
        except Exception as error:
            current = self._runs.get(request.run.extraction_run_id)
            if current and current.status == "RUNNING":
                self._runs.fail(
                    request.run.extraction_run_id,
                    str(error)[:500] or "Document extraction failed.",
                )
            document = self._documents.get(request.document.document_id)
            if document and document.status == "EXTRACTING":
                self._documents.update_status(
                    document.document_id, {"EXTRACTING"}, "EXTRACT_FAILED"
                )


class DatabricksExtractionJobRunner:
    def __init__(self, client: WorkspaceClient, job_id: int) -> None:
        self._client = client
        self._job_id = job_id

    def trigger(self, request: ExtractionJobRequest) -> int:
        wait = self._client.jobs.run_now(
            self._job_id,
            # Each immutable retry is a distinct Jobs invocation. The governed source
            # idempotency key remains visible in extraction_runs.options.
            idempotency_token=request.run.extraction_run_id,
            job_parameters={
                "document_id": request.document.document_id,
                "extraction_run_id": request.run.extraction_run_id,
                "schema_id": request.schema.schema_id,
                "schema_version": str(request.schema.schema_version),
                "requested_by": request.run.requested_by,
            },
        )
        run = cast(jobs.Run, wait.response)
        if run.run_id is None:
            raise RuntimeError("Databricks Jobs trigger did not return a run identifier")
        return run.run_id

    def poll(self, job_run_id: int) -> ExtractionJobPoll:
        run = self._client.jobs.get_run(job_run_id)
        if run.state is None or run.state.life_cycle_state in {
            jobs.RunLifeCycleState.PENDING,
            jobs.RunLifeCycleState.QUEUED,
            jobs.RunLifeCycleState.RUNNING,
            jobs.RunLifeCycleState.TERMINATING,
            jobs.RunLifeCycleState.WAITING_FOR_RETRY,
        }:
            return ExtractionJobPoll(ExtractionJobState.RUNNING)
        if run.state.result_state in {
            jobs.RunResultState.SUCCESS,
            jobs.RunResultState.SUCCESS_WITH_FAILURES,
        }:
            return ExtractionJobPoll(ExtractionJobState.SUCCEEDED)
        return ExtractionJobPoll(
            ExtractionJobState.FAILED,
            (run.state.state_message or "Databricks extraction job failed.")[:500],
        )


def _mock_ai_extract(parse_run: ParseRunRecord, schema: SchemaRecord) -> dict[str, Any]:
    text = parse_run.document_text or ""
    citation = _first_citation(parse_run)
    response: dict[str, Any] = {}
    for path, definition in schema.ai_extract_schema.items():
        if definition.type == "array":
            response[path] = _mock_line_items(definition, text, citation)
            continue
        value = _mock_value(path, definition.type, text)
        response[path] = _wrap(value, citation)
    return {
        "response": response,
        "error_message": None,
        "metadata": {
            "version": "2.1",
            "mode": "precision",
            "chunk_type": "bbox",
            "citations": [citation] if citation is not None else [],
        },
    }


# The local mock reads a deliberately simple line form from the generated fixture PDFs, so mock
# mode exercises the same nested response shape that ai_extract returns.
LINE_ITEM = re.compile(
    r"^LINE:\s*(?P<quantity>\d+(?:\.\d+)?)\s*x\s*(?P<description>.+?)\s*@\s*"
    r"(?P<unit_price>[\d,]+\.?\d*)\s*tax\s*(?P<tax>[\d,]+\.?\d*)\s*=\s*"
    r"(?P<amount>[\d,]+\.?\d*)\s*$",
    flags=re.IGNORECASE | re.MULTILINE,
)


def _wrap(value: object, citation: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "value": value,
        "citation_ids": [0] if value is not None and citation is not None else [],
        "confidence_score": 0.99 if value is not None else None,
    }


def _mock_line_items(
    definition: ExtractField, text: str, citation: dict[str, Any] | None
) -> list[dict[str, Any]]:
    item = definition.items
    properties = item.properties if item is not None else None
    if not properties:
        return []
    elements: list[dict[str, Any]] = []
    for match in LINE_ITEM.finditer(text):
        element: dict[str, Any] = {}
        for name, leaf in properties.items():
            raw = match.groupdict().get(name)
            value: object = None
            if raw is not None:
                value = _coerce(raw.strip(), leaf.type)
            element[name] = _wrap(value, citation)
        elements.append(element)
    return elements


def _coerce(raw: str, field_type: str) -> object:
    if field_type == "number":
        try:
            return float(raw.replace(",", ""))
        except ValueError:
            return None
    if field_type == "integer":
        try:
            return int(float(raw.replace(",", "")))
        except ValueError:
            return None
    return raw


def _first_citation(parse_run: ParseRunRecord) -> dict[str, Any] | None:
    parsed = parse_run.parsed or {}
    document = parsed.get("document")
    elements = document.get("elements", []) if isinstance(document, dict) else []
    for element in elements:
        if isinstance(element, dict) and isinstance(element.get("bbox"), list):
            return {"id": 0, "bbox": element["bbox"]}
    return None


def _mock_value(path: str, field_type: str, text: str) -> object:
    aliases = {
        "invoice_number": r"(?:invoice\s*(?:number|no\.?|#)\s*[:#-]?\s*)([^\n]+)",
        "invoice_date": r"(?:invoice\s*date|date)\s*[:#-]?\s*(\d{4}-\d{2}-\d{2})",
        "seller_name": r"(?:seller|vendor)\s*(?:name)?\s*[:#-]?\s*([^\n]+)",
        "subtotal": r"subtotal\s*[:#-]?\s*(?:[A-Z]{3}\s*)?([-+]?\d[\d,]*\.\d{1,2})",
        "discount": r"discount\s*[:#-]?\s*(?:[A-Z]{3}\s*)?([-+]?\d[\d,]*\.\d{1,2})",
        "tax": r"(?:tax|vat)\s*[:#-]?\s*(?:[A-Z]{3}\s*)?([-+]?\d[\d,]*\.\d{1,2})",
        "total": r"(?<!sub)total\s*[:#-]?\s*(?:[A-Z]{3}\s*)?([-+]?\d[\d,]*\.\d{1,2})",
        "currency": r"currency\s*[:#-]?\s*([A-Z]{3})",
    }
    pattern = aliases.get(path, rf"{re.escape(path.replace('_', ' '))}\s*[:#-]?\s*([^\n]+)")
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match is None:
        return None
    raw = match.group(1).strip().rstrip()
    if field_type == "number":
        try:
            return float(raw.replace(",", ""))
        except ValueError:
            return None
    if field_type == "integer":
        try:
            return int(raw.replace(",", ""))
        except ValueError:
            return None
    if field_type == "boolean":
        return raw.lower() in {"true", "yes", "1"}
    return raw
