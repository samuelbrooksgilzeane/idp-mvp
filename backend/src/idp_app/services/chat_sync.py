"""Best-effort, coalesced Knowledge Assistant Sync after documents are added or deleted.

The KA indexes the source volume's PDFs only when its sources are synced. A change marks a sync
pending; it runs once changes have been quiet for QUIET_SECONDS (so an upload batch or folder
import syncs once, at its end) and never sooner than MIN_INTERVAL_SECONDS after the previous one.
A failed or skipped sync is not retried: the next change, or a manual Sync, catches up.
"""

import logging
import re
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

QUIET_SECONDS = 60.0
MIN_INTERVAL_SECONDS = 600.0
KA_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class KnowledgeSync:
    def __init__(
        self,
        sync: Callable[[], object],
        *,
        clock: Callable[[], float] = time.monotonic,
        schedule: Callable[[float, Callable[[], None]], object] | None = None,
    ) -> None:
        self._sync = sync
        self._clock = clock
        self._schedule = schedule or _daemon_timer
        self._lock = threading.Lock()
        self._pending = False
        self._due = 0.0
        self._last_sync = float("-inf")
        self._timer_due: float | None = None

    def request(self) -> None:
        """Mark a sync pending; returns at once."""
        with self._lock:
            self._pending = True
            now = self._clock()
            self._due = max(now + QUIET_SECONDS, self._last_sync + MIN_INTERVAL_SECONDS)
            if self._timer_due is None:
                self._arm(self._due - now)

    def flush(self) -> None:
        """Run a pending sync now, ignoring the quiet period, for processes about to exit."""
        with self._lock:
            if not self._pending:
                return
            self._pending = False
            self._last_sync = self._clock()
        self._run()

    def _arm(self, delay: float) -> None:
        self._timer_due = self._clock() + delay
        self._schedule(max(delay, 0.0), self._fire)

    def _fire(self) -> None:
        with self._lock:
            self._timer_due = None
            if not self._pending:
                return
            now = self._clock()
            if now < self._due:
                self._arm(self._due - now)  # more changes arrived; wait for quiet again
                return
            self._pending = False
            self._last_sync = now
        self._run()

    def _run(self) -> None:
        try:
            self._sync()
        except Exception:
            logger.warning("Knowledge Assistant sync request failed; the next change retries")


def _daemon_timer(delay: float, callback: Callable[[], None]) -> object:
    timer = threading.Timer(delay, callback)
    timer.daemon = True
    timer.start()
    return timer


def databricks_sync(knowledge_assistant_id: str) -> Callable[[], object]:
    if KA_ID.fullmatch(knowledge_assistant_id) is None:
        raise ValueError("IDP_KA_ID must be a Knowledge Assistant UUID")
    path = f"/api/2.1/knowledge-assistants/{knowledge_assistant_id}/knowledge-sources:sync"

    def sync() -> object:
        from databricks.sdk import WorkspaceClient

        return WorkspaceClient().api_client.do("POST", path, body={})

    return sync


_shared: dict[str, KnowledgeSync] = {}
_shared_lock = threading.Lock()


def shared_knowledge_sync(knowledge_assistant_id: str) -> KnowledgeSync:
    """One coalescer per KA per process, shared by every service that changes documents."""
    with _shared_lock:
        existing = _shared.get(knowledge_assistant_id)
        if existing is None:
            existing = KnowledgeSync(databricks_sync(knowledge_assistant_id))
            _shared[knowledge_assistant_id] = existing
        return existing
