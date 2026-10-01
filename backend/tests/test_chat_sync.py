"""KA Sync coalescing with a fake clock and a manual scheduler; no threads or workspace."""

import pytest

from idp_app.core.config import IdpMode, Settings
from idp_app.services import chat_sync
from idp_app.services.chat_sync import MIN_INTERVAL_SECONDS, QUIET_SECONDS, KnowledgeSync

KA = "921c152f-f4b9-4ce6-b524-e369134e9958"


class Harness:
    def __init__(self, fail: bool = False) -> None:
        self.now = 0.0
        self.syncs: list[float] = []
        self.timers: list[tuple[float, object]] = []
        self.fail = fail
        self.sync = KnowledgeSync(self._sync, clock=lambda: self.now, schedule=self._schedule)

    def _sync(self) -> None:
        self.syncs.append(self.now)
        if self.fail:
            raise RuntimeError("sync rejected")

    def _schedule(self, delay: float, callback) -> None:
        self.timers.append((self.now + delay, callback))

    def advance(self, seconds: float) -> None:
        """Move time forward, firing timers in due order (including ones they arm)."""
        end = self.now + seconds
        while True:
            due = sorted((at, cb) for at, cb in self.timers if at <= end)
            if not due:
                break
            at, callback = due[0]
            self.timers.remove((at, callback))
            self.now = at
            callback()
        self.now = end


def test_a_burst_of_changes_syncs_once_after_it_goes_quiet() -> None:
    h = Harness()
    for _ in range(5):
        h.sync.request()
        h.advance(20)
    assert h.syncs == []  # still busy: each change restarts the quiet period
    h.advance(QUIET_SECONDS)
    assert h.syncs == [80 + QUIET_SECONDS]
    h.advance(3600)
    assert len(h.syncs) == 1
    assert len(h.timers) == 0


def test_syncs_are_at_least_the_minimum_interval_apart_and_trailing_changes_kept() -> None:
    h = Harness()
    h.sync.request()
    h.advance(QUIET_SECONDS)
    first = h.syncs[0]
    h.advance(30)
    h.sync.request()  # a deletion shortly after
    h.advance(QUIET_SECONDS)
    assert h.syncs == [first]
    h.advance(MIN_INTERVAL_SECONDS)
    assert h.syncs == [first, first + MIN_INTERVAL_SECONDS]


def test_only_one_timer_is_armed_at_a_time() -> None:
    h = Harness()
    for _ in range(100):
        h.sync.request()
    assert len(h.timers) == 1


def test_flush_syncs_immediately_and_only_when_pending() -> None:
    h = Harness()
    h.sync.flush()
    assert h.syncs == []
    h.sync.request()
    h.sync.flush()
    assert h.syncs == [0.0]
    h.advance(3600)  # the armed timer finds nothing pending
    assert h.syncs == [0.0]


def test_a_failed_sync_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    h = Harness(fail=True)
    h.sync.request()
    h.advance(QUIET_SECONDS)
    assert len(h.syncs) == 1
    assert "sync request failed" in caplog.text


def test_databricks_sync_posts_to_the_sync_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class Client:
        api_client = type("Api", (), {"do": lambda self, *a, **k: calls.append((a, k))})()

    monkeypatch.setattr("databricks.sdk.WorkspaceClient", lambda: Client())
    chat_sync.databricks_sync(KA)()
    assert calls == [
        (("POST", f"/api/2.1/knowledge-assistants/{KA}/knowledge-sources:sync"), {"body": {}})
    ]
    with pytest.raises(ValueError):
        chat_sync.databricks_sync("../other")


def test_settings_require_a_ka_id_when_sync_is_enabled() -> None:
    base = {
        "_env_file": None,
        "mode": IdpMode.DATABRICKS,
        "catalog": "workspace",
        "project_schema": "idp_mvp",
        "table_prefix": "idp_dev",
        "source_volume_name": "idp_source",
        "artifacts_volume_name": "idp_artifacts",
        "warehouse_id": "abc",
        "parse_job_id": 1,
        "extraction_job_id": 1,
        "validation_endpoint": "unused",
    }
    assert Settings(**base, ka_id=" ").ka_id is None
    assert Settings(**base, ka_sync_enabled=True, ka_id=KA).ka_id == KA
    with pytest.raises(ValueError, match="IDP_KA_ID"):
        Settings(**base, ka_sync_enabled=True, ka_id=" ")
    with pytest.raises(ValueError):
        Settings(**base, ka_id="not-a-uuid")
