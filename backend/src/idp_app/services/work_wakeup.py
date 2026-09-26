"""Best-effort coalesced wake-up; durable rows and recovery schedule own reliability."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="idp-work-wakeup")
_lock = Lock()
_last: dict[int, float] = {}


def wake_dispatcher(job_id: int) -> None:
    with _lock:
        now = time.monotonic()
        if now - _last.get(job_id, float("-inf")) < 60:
            return
        _last[job_id] = now
    _executor.submit(_trigger, job_id)


def _trigger(job_id: int) -> None:
    try:
        # Stable across app replicas. Dispatcher max_concurrent_runs=1 and queue=false.
        WorkspaceClient().jobs.run_now(
            job_id, idempotency_token=f"wake-{job_id}-{int(time.time() // 60)}"
        )
    except Exception:
        logger.warning("Dispatcher wake-up failed; durable queue awaits scheduled recovery")
