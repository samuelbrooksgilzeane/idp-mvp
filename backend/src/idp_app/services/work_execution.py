"""Task-side ownership checks shared by the local worker and deployed parser."""

from dataclasses import dataclass
from uuid import uuid4

from idp_app.services.document_models import DocumentRecord, ParseRunRecord
from idp_app.services.preparation import PreparationService
from idp_app.services.work_batches import WorkItem, changed, now_iso


@dataclass(frozen=True)
class ClaimedParse:
    item: WorkItem
    document: DocumentRecord
    run: ParseRunRecord


def begin_parse(
    preparation: PreparationService, dispatch_id: str, identity: str
) -> ClaimedParse | None:
    dispatch = preparation.work.dispatch(dispatch_id)
    item = preparation.work.item(identity)
    if (
        dispatch is None
        or identity not in dispatch.work_item_ids
        or dispatch.state not in {"SUBMITTING", "ASSIGNING", "RUNNING"}
    ):
        raise ValueError("Task is not in an active dispatch")
    if item is None or item.dispatch_id != dispatch_id:
        raise ValueError("Task does not own the queued work")
    run = preparation.ensure_run(item)
    if run.status == "SUCCESS":
        return None
    if item.state != "CLAIMED" or not item.lease_expires_at or item.lease_expires_at <= now_iso():
        raise ValueError("Task requires a valid, unconsumed claim")
    document = preparation.documents.get(item.document_id)
    if (
        document is None
        or document.status == "DELETED"
        or document.content_sha256 != item.content_sha256
    ):
        preparation.runs.fail(
            run.parse_run_id,
            {
                "code": "SOURCE_CHANGED",
                "error_message": "The registered source changed or was deleted.",
            },
        )
        return None
    if run.document_id != document.document_id or run.content_sha256 != document.content_sha256:
        raise ValueError("Pinned parse identity does not match the registered source")
    claimed = changed(item, state="RUNNING", execution_owner=str(uuid4()))
    if not preparation.work.update_item(item, claimed):
        raise ValueError("Another task already consumed this claim")
    preparation.runs.activate(run.parse_run_id)
    preparation.mark_document(claimed, "PARSING")
    return ClaimedParse(claimed, document, run)


def check_owner(preparation: PreparationService, claimed: ClaimedParse) -> None:
    current = preparation.work.item(claimed.item.work_item_id)
    if (
        current is None
        or current.state != "RUNNING"
        or current.dispatch_id != claimed.item.dispatch_id
        or current.parse_run_id != claimed.run.parse_run_id
        or current.execution_owner != claimed.item.execution_owner
    ):
        raise ValueError("Task ownership changed; refusing a stale result")


def finish_parse(preparation: PreparationService, claimed: ClaimedParse) -> None:
    """Idempotent per-item terminal publication after the immutable run is durable."""
    current = preparation.work.item(claimed.item.work_item_id)
    if (
        current
        and current.parse_run_id == claimed.run.parse_run_id
        and current.state in {"SUCCEEDED", "FAILED"}
    ):
        return
    check_owner(preparation, claimed)
    run = preparation.runs.get(claimed.run.parse_run_id)
    if run is None or run.status not in {"SUCCESS", "FAILED"}:
        return  # A retained partial result is finalized by the central reconciler.
    assert current is not None
    code = run.parse_error.get("code") if isinstance(run.parse_error, dict) else None
    updated = changed(
        current,
        state="SUCCEEDED" if run.status == "SUCCESS" else "FAILED",
        lease_expires_at=None,
        error_code=code,
    )
    if not preparation.work.update_item(current, updated):
        raise ValueError("Task outcome changed during terminal publication")
