"""ID-only extraction manifests with stable submission identity."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import cast

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs

from idp_app.services.extraction_execution import execute_extraction
from idp_app.services.extraction_jobs import _mock_ai_extract
from idp_app.services.extraction_queue import ExtractionQueue
from idp_app.services.parse_jobs import DatabricksParseJobRunner, ParseJobPoll, ParseJobState
from idp_app.services.work_dispatch import manifest_ids


class DatabricksExtractionManifestJobs(DatabricksParseJobRunner):
    def __init__(self, client: WorkspaceClient, job_id: int) -> None:
        super().__init__(client, job_id)
        self.client, self.job_id = client, job_id

    def trigger_manifest(self, dispatch_id: str) -> int:
        response = cast(
            jobs.Run,
            self.client.jobs.run_now(
                self.job_id,
                idempotency_token=f"extract-{dispatch_id}",
                job_parameters={"dispatch_id": dispatch_id},
            ).response,
        )
        if response.run_id is None:
            raise RuntimeError("Extraction Job did not return a run identity")
        return response.run_id


class LocalExtractionManifestJobs:
    def __init__(self, queue: ExtractionQueue) -> None:
        self.queue = queue
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="idp-extraction-manifest"
        )
        self.futures: dict[int, Future[None]] = {}
        self.tokens: dict[str, int] = {}
        self.lock = Lock()

    def trigger_manifest(self, dispatch_id: str) -> int:
        with self.lock:
            if dispatch_id in self.tokens:
                return self.tokens[dispatch_id]
            dispatch = self.queue.work.dispatch(dispatch_id)
            if dispatch is None:
                raise ValueError("Manifest not found")
            ids = manifest_ids(dispatch, self.queue.work)
            identity = len(self.tokens) + 100000
            self.tokens[dispatch_id] = identity
            self.futures[identity] = self.executor.submit(self._execute, dispatch_id, ids)
            return identity

    def _execute(self, dispatch_id: str, identities: list[str]) -> None:
        failed = False
        for identity in identities:
            try:
                execute_extraction(
                    self.queue,
                    dispatch_id,
                    identity,
                    lambda document, parse, schema: _mock_ai_extract(parse, schema),
                )
            except Exception:
                failed = True
        if failed:
            raise RuntimeError("One or more mock extraction tasks failed")

    def poll(self, job_run_id: int) -> ParseJobPoll:
        future = self.futures.get(job_run_id)
        if future is None:
            return ParseJobPoll(
                ParseJobState.FAILED, "Local worker restarted; reconcile saved results."
            )
        if not future.done():
            return ParseJobPoll(ParseJobState.RUNNING)
        return ParseJobPoll(ParseJobState.FAILED if future.exception() else ParseJobState.SUCCEEDED)
