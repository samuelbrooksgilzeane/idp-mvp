from __future__ import annotations

import itertools
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Protocol, cast

import pymupdf
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs

from idp_app.services.document_models import DocumentRecord
from idp_app.services.document_registry import DocumentRegistry
from idp_app.services.job_batches import batch_idempotency_token, encode_inputs
from idp_app.services.parse_runs import ParseRunRepository


@dataclass(frozen=True)
class ParseJobRequest:
    parse_run_id: str
    document: DocumentRecord
    page_image_root: str


class ParseJobState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ParseJobPoll:
    state: ParseJobState
    message: str | None = None


class ParseJobRunner(Protocol):
    def trigger(self, requests: list[ParseJobRequest]) -> int: ...

    def poll(self, job_run_id: int) -> ParseJobPoll: ...


class MockParseJobRunner:
    def __init__(
        self,
        parse_runs: ParseRunRepository,
        documents: DocumentRegistry,
        *,
        delay_seconds: float = 0.15,
    ) -> None:
        self._parse_runs = parse_runs
        self._documents = documents
        self._delay_seconds = delay_seconds
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="idp-parser")
        self._ids = itertools.count(1)
        self._futures: dict[int, list[Future[None]]] = {}
        self._lock = Lock()

    def trigger(self, requests: list[ParseJobRequest]) -> int:
        if not requests:
            raise ValueError("A parse batch requires at least one document")
        job_run_id = next(self._ids)
        submitted = [self._executor.submit(self._execute, request) for request in requests]
        with self._lock:
            self._futures[job_run_id] = submitted
        return job_run_id

    def poll(self, job_run_id: int) -> ParseJobPoll:
        with self._lock:
            futures = self._futures.get(job_run_id)
        if futures is None:
            return ParseJobPoll(ParseJobState.FAILED, "Mock parse job was not found.")
        if any(not item.done() for item in futures):
            return ParseJobPoll(ParseJobState.RUNNING)
        # One failing document fails the run, matching a for_each iteration failure. Each
        # document's own terminal state is already recorded against its immutable run.
        if any(item.exception() is not None for item in futures):
            return ParseJobPoll(ParseJobState.FAILED, "Mock parse job failed.")
        return ParseJobPoll(ParseJobState.SUCCEEDED)

    def _execute(self, request: ParseJobRequest) -> None:
        if self._delay_seconds:
            time.sleep(self._delay_seconds)
        try:
            parsed, document_text, page_count = _parse_pdf(
                Path(request.document.source_path),
                Path(request.page_image_root),
                request.parse_run_id,
            )
            self._parse_runs.complete(
                request.parse_run_id,
                parsed,
                document_text,
                page_count,
            )
            self._documents.update_status(
                request.document.document_id,
                {"PARSING"},
                "PARSED",
            )
        except Exception as error:
            failure = {"error_message": str(error)[:500] or "Document parsing failed."}
            current_run = self._parse_runs.get(request.parse_run_id)
            if current_run and current_run.status == "RUNNING":
                self._parse_runs.fail(request.parse_run_id, failure)
            current_document = self._documents.get(request.document.document_id)
            if current_document and current_document.status == "PARSING":
                self._documents.update_status(
                    request.document.document_id,
                    {"PARSING"},
                    "PARSE_FAILED",
                )


class DatabricksParseJobRunner:
    def __init__(self, client: WorkspaceClient, job_id: int) -> None:
        self._client = client
        self._job_id = job_id

    def trigger(self, requests: list[ParseJobRequest]) -> int:
        if not requests:
            raise ValueError("A parse batch requires at least one document")
        inputs = [
            {
                "document_id": request.document.document_id,
                "parse_run_id": request.parse_run_id,
                "source_path": request.document.source_path,
                "image_output_path": f"{request.page_image_root.rstrip('/')}/",
            }
            for request in requests
        ]
        wait = self._client.jobs.run_now(
            self._job_id,
            idempotency_token=batch_idempotency_token(
                request.parse_run_id for request in requests
            ),
            job_parameters={"inputs": encode_inputs(inputs)},
        )
        run = cast(jobs.Run, wait.response)
        if run.run_id is None:
            raise RuntimeError("Databricks Jobs trigger did not return a run identifier")
        return run.run_id

    def poll(self, job_run_id: int) -> ParseJobPoll:
        run = self._client.jobs.get_run(job_run_id)
        if run.state is None or run.state.life_cycle_state in {
            jobs.RunLifeCycleState.PENDING,
            jobs.RunLifeCycleState.QUEUED,
            jobs.RunLifeCycleState.RUNNING,
            jobs.RunLifeCycleState.TERMINATING,
            jobs.RunLifeCycleState.WAITING_FOR_RETRY,
        }:
            return ParseJobPoll(ParseJobState.RUNNING)
        if run.state.result_state in {
            jobs.RunResultState.SUCCESS,
            jobs.RunResultState.SUCCESS_WITH_FAILURES,
        }:
            return ParseJobPoll(ParseJobState.SUCCEEDED)
        return ParseJobPoll(
            ParseJobState.FAILED,
            (run.state.state_message or "Databricks parse job failed.")[:500],
        )


def _parse_pdf(
    source_path: Path,
    image_root: Path,
    parse_run_id: str,
) -> tuple[dict[str, Any], str, int]:
    image_root.mkdir(parents=True, exist_ok=True)
    pages: list[dict[str, Any]] = []
    elements: list[dict[str, Any]] = []
    text_parts: list[str] = []
    with pymupdf.open(source_path) as document:
        if document.page_count == 0:
            raise ValueError("PDF contains no pages")
        for page_index, page in enumerate(document):
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
            image_path = image_root / f"page-{page_index + 1}.png"
            pixmap.save(image_path)
            pages.append({"id": page_index, "image_uri": image_path.as_posix()})

            blocks = cast(list[tuple[Any, ...]], page.get_text("blocks"))
            for block in blocks:
                x0, y0, x1, y1, content = block[:5]
                text = str(content).strip()
                if not text:
                    continue
                text_parts.append(text)
                elements.append(
                    {
                        "id": len(elements),
                        "type": "text",
                        "content": text,
                        "confidence": 1.0,
                        "bbox": [
                            {
                                "coord": [
                                    round(float(x0) * 1.5),
                                    round(float(y0) * 1.5),
                                    round(float(x1) * 1.5),
                                    round(float(y0) * 1.5),
                                    round(float(x1) * 1.5),
                                    round(float(y1) * 1.5),
                                    round(float(x0) * 1.5),
                                    round(float(y1) * 1.5),
                                ],
                                "page_id": page_index,
                            }
                        ],
                        "description": None,
                    }
                )

    parsed = {
        "document": {"pages": pages, "elements": elements},
        "error_status": [],
        "metadata": {"id": parse_run_id, "version": "2.0"},
    }
    return parsed, "\n\n".join(text_parts), len(pages)
