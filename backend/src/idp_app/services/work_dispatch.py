"""Serialized, finite dispatch steps. No inference or busy polling in the app process."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from idp_app.services.parse_jobs import ParseJobPoll, ParseJobState
from idp_app.services.preparation import PreparationService
from idp_app.services.work_batches import WorkDispatch, WorkItem, WorkRepository, changed, now_iso


class ManifestJobRunner(Protocol):
    def trigger_manifest(self, dispatch_id: str) -> int: ...
    def poll(self, job_run_id: int) -> ParseJobPoll: ...


def manifest_ids(dispatch: WorkDispatch, work: WorkRepository) -> list[str]:
    if dispatch.state not in {"SUBMITTING", "ASSIGNING", "RUNNING"}:
        raise ValueError("Dispatch is not submitted")
    ids = []
    for identity in dispatch.work_item_ids:
        item = work.item(identity)
        if (
            item
            and item.dispatch_id == dispatch.dispatch_id
            and item.state in {"CLAIMED", "RUNNING"}
        ):
            ids.append(identity)
    if len(ids) > 100 or len(json.dumps(ids).encode()) >= 48 * 1024:
        raise ValueError("Work manifest exceeds the bounded task-value contract")
    return ids


class WorkDispatcher:
    def __init__(
        self,
        preparation: PreparationService,
        jobs: ManifestJobRunner,
        *,
        size: int = 100,
        max_attempts: int = 3,
        lease_seconds: int = 7200,
    ) -> None:
        if not 1 <= size <= 100:
            raise ValueError("Dispatch size must be between 1 and 100")
        self.preparation, self.work, self.jobs = preparation, preparation.work, jobs
        self.size, self.max_attempts, self.lease_seconds = size, max_attempts, lease_seconds
        self.owner = str(uuid4())
        self.last_heartbeat = 0.0

    def _lock(self) -> WorkDispatch | None:
        identity = "parse-dispatcher-lock"
        self.work.put_dispatch(WorkDispatch(identity, [], state="LOCK"))
        lock = self.work.dispatch(identity)
        if lock is None:
            raise RuntimeError("Dispatcher lock disappeared")
        if lock.lease_owner and lock.lease_expires_at and lock.lease_expires_at > now_iso():
            return None
        claimed = changed(
            lock,
            lease_owner=self.owner,
            lease_expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        )
        return claimed if self.work.update_dispatch(lock, claimed) else None

    def _heartbeat(self) -> None:
        if time.monotonic() - self.last_heartbeat < 30:
            return
        lock = self.work.dispatch("parse-dispatcher-lock")
        if (
            lock is None
            or lock.lease_owner != self.owner
            or not lock.lease_expires_at
            or lock.lease_expires_at <= now_iso()
        ):
            raise RuntimeError("Dispatcher lease lost; stop before submitting work")
        renewed = changed(
            lock, lease_expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        )
        if not self.work.update_dispatch(lock, renewed):
            raise RuntimeError("Dispatcher lease changed")
        self.last_heartbeat = time.monotonic()

    def tick(self) -> bool:
        """Perform one bounded step; True means a later step may make progress."""
        lock = self._lock()
        if lock is None:
            return False
        self.last_heartbeat = time.monotonic()
        try:
            active = self.work.active_dispatches()
            if len(active) > 1:
                raise RuntimeError("Multiple active parse dispatches need operator reconciliation")
            if active:
                dispatch = active[0]
            else:
                self.preparation.reconcile_missing_intents(max_documents=self.size)
                candidates = self.work.candidates(self.size)
                if not candidates:
                    return bool(self.work.summary()["counts"].get("QUEUED", 0))
                now = now_iso()
                dispatch = WorkDispatch(
                    str(uuid4()),
                    [item.work_item_id for item in candidates],
                    created_at=now,
                    updated_at=now,
                )
                self._heartbeat()
                self.work.put_dispatch(dispatch)
            if dispatch.state == "PREPARING":
                for identity in dispatch.work_item_ids:
                    self._heartbeat()
                    item = self.work.item(identity)
                    if item is None:
                        raise RuntimeError("Manifest member disappeared")
                    if item.state == "QUEUED":
                        claimed = changed(
                            item,
                            state="CLAIMED",
                            dispatch_id=dispatch.dispatch_id,
                            attempts=item.attempts + 1,
                            lease_expires_at=(
                                datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
                            ).isoformat(),
                        )
                        if not self.work.update_item(item, claimed):
                            raise RuntimeError("Manifest claim changed; retry preparation")
                        item = claimed
                    if item.dispatch_id != dispatch.dispatch_id:
                        raise RuntimeError("Manifest member belongs to another dispatch")
                    self.preparation.ensure_run(item)
                submitted = changed(dispatch, state="SUBMITTING")
                if not self.work.update_dispatch(dispatch, submitted):
                    raise RuntimeError("Dispatch changed before submission")
                dispatch = submitted
            if dispatch.state == "SUBMITTING":
                self._heartbeat()
                # A timeout leaves SUBMITTING intact. Reuse this exact token on recovery.
                job_id = self.jobs.trigger_manifest(dispatch.dispatch_id)
                running = changed(dispatch, state="ASSIGNING", job_run_id=job_id)
                if not self.work.update_dispatch(dispatch, running):
                    raise RuntimeError("Dispatch submission needs reconciliation")
                dispatch = running
            if dispatch.job_run_id is None:
                raise RuntimeError("Running dispatch has no Job identity")
            if dispatch.state == "ASSIGNING":
                for identity in dispatch.work_item_ids:
                    self._heartbeat()
                    item = self.work.item(identity)
                    if item and item.dispatch_id == dispatch.dispatch_id:
                        self.preparation.runs.assign_job_run(item.parse_run_id, dispatch.job_run_id)
                running = changed(dispatch, state="RUNNING")
                if not self.work.update_dispatch(dispatch, running):
                    raise RuntimeError("Job assignment needs reconciliation")
                dispatch = running
            assert dispatch.job_run_id is not None
            poll = self.jobs.poll(dispatch.job_run_id)
            if poll.state in {ParseJobState.PENDING, ParseJobState.RUNNING}:
                # Never reclaim an expired item while its Job is still active.
                return True
            for identity in dispatch.work_item_ids:
                self._heartbeat()
                item = self.work.item(identity)
                if item is not None and item.dispatch_id == dispatch.dispatch_id:
                    self._settle(item)
            terminal = changed(
                dispatch, state="SUCCEEDED" if poll.state is ParseJobState.SUCCEEDED else "FAILED"
            )
            if not self.work.update_dispatch(dispatch, terminal):
                raise RuntimeError("Dispatch completion needs reconciliation")
            return True
        finally:
            current = self.work.dispatch("parse-dispatcher-lock")
            if current and current.lease_owner == self.owner:
                self.work.update_dispatch(
                    current, changed(current, lease_owner=None, lease_expires_at=None)
                )

    def _settle(self, item: WorkItem) -> None:
        if item.state == "SUCCEEDED":
            self.preparation.mark_document(item, "PARSED")
            return
        if item.state == "QUEUED":
            return
        run = self.preparation.runs.get(item.parse_run_id)
        if run is None:
            raise RuntimeError("Parse run disappeared during recovery")
        # Retained inference is sufficient to finish projection after a task crash.
        if run.status in {"QUEUED", "RUNNING"} and run.parsed is not None:
            errors = run.parsed.get("error_status", [])
            if errors:
                self.preparation.runs.fail(run.parse_run_id, errors)
            else:
                content = run.parsed.get("document", {})
                self.preparation.runs.activate(run.parse_run_id)
                self.preparation.runs.complete(
                    run.parse_run_id,
                    run.parsed,
                    "\n\n".join(
                        str(element.get("content") or "") for element in content.get("elements", [])
                    ),
                    len(content.get("pages", [])),
                )
            run = self.preparation.runs.get(item.parse_run_id)
            assert run is not None
        if run.status == "SUCCESS":
            updated = changed(
                item, state="SUCCEEDED", error_code=None, error_message=None, lease_expires_at=None
            )
            if self.work.update_item(item, updated):
                self.preparation.mark_document(updated, "PARSED")
            return
        transient = run.status in {"QUEUED", "RUNNING"} or (
            isinstance(run.parse_error, dict)
            and run.parse_error.get("code") == "TRANSIENT_PARSE_ERROR"
        )
        if run.status in {"QUEUED", "RUNNING"}:
            self.preparation.runs.fail(
                run.parse_run_id,
                {
                    "code": "JOB_DID_NOT_COMMIT",
                    "error_message": "The Job ended without committing a parse result.",
                },
            )
        retry = transient and item.attempts < self.max_attempts
        updated = changed(
            item,
            state="QUEUED" if retry else "FAILED",
            parse_run_id=str(uuid4()) if retry else item.parse_run_id,
            dispatch_id=None if retry else item.dispatch_id,
            lease_expires_at=None,
            next_eligible_at=(
                datetime.now(UTC) + timedelta(seconds=30 * 2 ** max(0, item.attempts - 1))
            ).isoformat(),
            error_code="RETRY_PENDING" if retry else "PREPARATION_FAILED",
            error_message="Preparation will retry."
            if retry
            else "Preparation failed. Review the document and retry.",
        )
        if self.work.update_item(item, updated):
            self.preparation.mark_document(updated, "PARSE_QUEUED" if retry else "PARSE_FAILED")
            if retry:
                self.preparation.ensure_run(updated)
