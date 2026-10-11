"""Values the workspace_setup Job records in <prefix>_app_settings, read at run time.

Databricks generates the Knowledge Assistant's id and the chat's serving endpoint name when chat is
provisioned, so the setup Job writes them to a table instead of deploy variables, and a deploy
never waits for provisioning. Reads are cached for CACHE_SECONDS. While nothing is recorded, or
after a failed read (the last values are kept), the table is read again after RETRY_SECONDS.
"""

import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

CACHE_SECONDS = 300.0
RETRY_SECONDS = 30.0
CHAT_ENDPOINT = "chat_endpoint"
KA_ID = "ka_id"


class AppSettingsTable:
    def __init__(
        self,
        table: str,
        execute_sql: Callable[[str], list[list[str]]],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.table = table
        self._execute_sql = execute_sql
        self._clock = clock
        self._lock = threading.Lock()
        self._values: dict[str, str] = {}
        self._expires = float("-inf")

    def get(self, name: str) -> str | None:
        with self._lock:
            now = self._clock()
            if now >= self._expires:
                try:
                    rows = self._execute_sql(f"SELECT name, value FROM {self.table}")
                    self._values = {row[0]: row[1] for row in rows if row[1]}
                    self._expires = now + (CACHE_SECONDS if self._values else RETRY_SECONDS)
                except Exception as error:
                    logger.warning("Reading %s failed: %s", self.table, type(error).__name__)
                    self._expires = now + RETRY_SECONDS
            return self._values.get(name)


_shared: dict[str, AppSettingsTable] = {}
_shared_lock = threading.Lock()


def shared_app_settings(
    table: str, execute_sql: Callable[[str], list[list[str]]]
) -> AppSettingsTable:
    """One cached reader per table per process, shared by chat and Knowledge Assistant Sync."""
    with _shared_lock:
        existing = _shared.get(table)
        if existing is None:
            existing = AppSettingsTable(table, execute_sql)
            _shared[table] = existing
        return existing
