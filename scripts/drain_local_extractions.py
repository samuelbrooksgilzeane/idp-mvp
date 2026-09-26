"""Explicit finite local mock queue drain; refuses Databricks mode."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend/src"))
from idp_app.core.config import IdpMode, Settings  # noqa: E402
from idp_app.services.extraction_manifest_jobs import LocalExtractionManifestJobs  # noqa: E402
from idp_app.services.extraction_queue_runtime import build_extraction_queue  # noqa: E402
from idp_app.services.work_dispatch import WorkDispatcher  # noqa: E402


def main():
    settings = Settings()
    if settings.mode is not IdpMode.MOCK:
        raise SystemExit("This command supports local mock mode only.")
    queue = build_extraction_queue(settings)
    jobs = LocalExtractionManifestJobs(queue)
    dispatcher = WorkDispatcher(queue, jobs)
    deadline = time.monotonic() + 3600
    try:
        while time.monotonic() < deadline and dispatcher.tick():
            time.sleep(1)
    finally:
        jobs.executor.shutdown(wait=True)


if __name__ == "__main__":
    main()
