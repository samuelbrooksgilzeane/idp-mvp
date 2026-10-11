"""The App's cached reader of values recorded by workspace_setup; no warehouse."""

import pytest

from idp_app.services.app_settings import CACHE_SECONDS, RETRY_SECONDS, AppSettingsTable


class Warehouse:
    def __init__(self) -> None:
        self.rows: list[list[str]] = []
        self.reads = 0
        self.fail = False

    def execute_sql(self, statement: str) -> list[list[str]]:
        assert statement == "SELECT name, value FROM c.s.idp_dev_app_settings"
        self.reads += 1
        if self.fail:
            raise RuntimeError("warehouse unavailable")
        return self.rows


def table(warehouse: Warehouse, now: list[float]) -> AppSettingsTable:
    return AppSettingsTable("c.s.idp_dev_app_settings", warehouse.execute_sql, clock=lambda: now[0])


def test_values_are_cached_then_reread() -> None:
    warehouse, now = Warehouse(), [0.0]
    warehouse.rows = [["chat_endpoint", "ka-1-endpoint"], ["ka_id", "k"]]
    settings = table(warehouse, now)

    assert settings.get("chat_endpoint") == "ka-1-endpoint"
    assert settings.get("ka_id") == "k" and warehouse.reads == 1

    warehouse.rows = [["chat_endpoint", "mas-2-endpoint"]]
    now[0] = CACHE_SECONDS
    assert settings.get("chat_endpoint") == "mas-2-endpoint" and warehouse.reads == 2


def test_nothing_recorded_is_retried_soon(caplog: pytest.LogCaptureFixture) -> None:
    warehouse, now = Warehouse(), [0.0]
    settings = table(warehouse, now)

    assert settings.get("chat_endpoint") is None
    now[0] = RETRY_SECONDS
    warehouse.rows = [["chat_endpoint", "ka-1-endpoint"]]
    assert settings.get("chat_endpoint") == "ka-1-endpoint"


def test_a_failed_read_keeps_the_last_values(caplog: pytest.LogCaptureFixture) -> None:
    warehouse, now = Warehouse(), [0.0]
    warehouse.rows = [["chat_endpoint", "ka-1-endpoint"]]
    settings = table(warehouse, now)
    settings.get("chat_endpoint")

    warehouse.fail = True
    now[0] = CACHE_SECONDS
    assert settings.get("chat_endpoint") == "ka-1-endpoint"
    assert "Reading c.s.idp_dev_app_settings failed: RuntimeError" in caplog.text
    assert settings.get("chat_endpoint") == "ka-1-endpoint" and warehouse.reads == 2
