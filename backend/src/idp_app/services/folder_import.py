"""Folder import: register PDFs a user copied into the import volume, without a browser.

Users copy a folder of PDFs to ``/Volumes/<catalog>/<schema>/<import volume>/<folder>/``, pick the
folder in the app, and a Job registers every PDF through the same upload-batch contract the
browser uses. Paths never come from the browser: the chosen folder must match a name from the
server's own listing, and each item's relative path is recorded when the batch is created.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Protocol

import anyio
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from fastapi import UploadFile
from starlette.datastructures import Headers

from idp_app.services.documents import DocumentServiceError
from idp_app.services.upload_batches import TERMINAL_UPLOAD_STATES, UploadBatchService, now_iso

logger = logging.getLogger(__name__)

# Bounds a listing of a deep or huge folder; far above the 1,000-file batch cap.
MAX_LISTED_ENTRIES = 20_000


@dataclass(frozen=True)
class ImportEntry:
    relative_path: str
    size: int


class ImportSource(Protocol):
    @property
    def root(self) -> str: ...

    def list_folders(self) -> list[str]: ...

    def list_files(self, folder: str) -> Iterator[ImportEntry]: ...

    def open(self, folder: str, relative_path: str) -> tuple[BinaryIO, int | None]: ...

    # No delete: a path names whatever file is there now, not the one that was registered, and
    # neither store can delete conditionally. Imported files stay until someone removes them.


def safe_relative(folder: str, relative_path: str = "") -> PurePosixPath:
    """The path below the import root, refusing anything that could leave it."""
    if not folder or "/" in folder or "\\" in folder or folder in {".", ".."}:
        raise ValueError("Import folder must be a single path component")
    relative = PurePosixPath(relative_path)
    if relative_path and (
        relative.is_absolute()
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("Import file path must stay inside its folder")
    return PurePosixPath(folder) / relative if relative_path else PurePosixPath(folder)


def _visible(name: str) -> bool:
    return bool(name) and not name.startswith(".") and not name.startswith("_")


class LocalImportSource:
    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> str:
        return self._root.as_posix()

    def list_folders(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(p.name for p in self._root.iterdir() if p.is_dir() and _visible(p.name))

    def list_files(self, folder: str) -> Iterator[ImportEntry]:
        base = self._root / safe_relative(folder)
        pending = [base]
        seen = 0
        while pending:
            directory = pending.pop()
            for path in sorted(directory.iterdir()):
                seen += 1
                if seen > MAX_LISTED_ENTRIES:
                    return
                if not _visible(path.name) or path.is_symlink():
                    continue
                if path.is_dir():
                    pending.append(path)
                elif path.is_file():
                    yield ImportEntry(path.relative_to(base).as_posix(), path.stat().st_size)

    def open(self, folder: str, relative_path: str) -> tuple[BinaryIO, int | None]:
        path = self._root / safe_relative(folder, relative_path)
        stream = path.open("rb")
        return stream, path.stat().st_size


class DatabricksImportSource:
    def __init__(
        self, client: WorkspaceClient, catalog: str, project_schema: str, volume_name: str
    ) -> None:
        self._client = client
        self._root = f"/Volumes/{catalog}/{project_schema}/{volume_name}"

    @property
    def root(self) -> str:
        return self._root

    def list_folders(self) -> list[str]:
        return sorted(
            entry.name
            for entry in self._client.files.list_directory_contents(self._root, page_size=1000)
            if entry.is_directory and entry.name and _visible(entry.name)
        )

    def list_files(self, folder: str) -> Iterator[ImportEntry]:
        base = f"{self._root}/{safe_relative(folder)}"
        pending = [base]
        seen = 0
        while pending:
            directory = pending.pop()
            for entry in self._client.files.list_directory_contents(directory, page_size=1000):
                seen += 1
                if seen > MAX_LISTED_ENTRIES:
                    return
                if not entry.path or not entry.name or not _visible(entry.name):
                    continue
                if entry.is_directory:
                    pending.append(entry.path.rstrip("/"))
                else:
                    yield ImportEntry(entry.path[len(base) + 1 :], entry.file_size or 0)

    def open(self, folder: str, relative_path: str) -> tuple[BinaryIO, int | None]:
        path = f"{self._root}/{safe_relative(folder, relative_path)}"
        try:
            response = self._client.files.download(path)
        except NotFound as error:
            raise FileNotFoundError(path) from error
        if response.contents is None:
            raise FileNotFoundError(path)
        length = response.content_length
        return response.contents, int(length) if length is not None else None


def client_file_id(relative_path: str) -> str:
    return hashlib.sha256(relative_path.encode()).hexdigest()[:40]


class FolderImportService:
    def __init__(
        self,
        source: ImportSource,
        uploads: UploadBatchService,
        start_run: Callable[[str, bool], object],
        concurrency: int = 8,
    ) -> None:
        self.source = source
        self.uploads = uploads
        self.start_run = start_run
        self.concurrency = concurrency

    def folders(self) -> dict[str, Any]:
        try:
            names = self.source.list_folders()
        except Exception as error:
            raise DocumentServiceError(
                "IMPORT_VOLUME_UNAVAILABLE", "The import volume could not be listed.", 503
            ) from error
        return {"root": self.source.root, "folders": [{"name": name} for name in names]}

    def start(
        self, requester: str, client_request_id: str, folder: str, case_id: str | None
    ) -> dict[str, Any]:
        request_id = f"folder:{client_request_id}"
        existing = self.uploads.repository.header(self.uploads.batch_id_for(requester, request_id))
        if existing is not None:
            # A replay (lost response): the Job may already have removed imported files, so
            # answer with the saved batch instead of listing the folder again.
            if existing.get("source") != "folder" or existing.get("folder") != folder:
                raise DocumentServiceError(
                    "BATCH_REPLAY_CONFLICT",
                    "This request identity belongs to a different import.",
                    409,
                )
            batch_id = existing["batch_id"]
            summary = self.uploads.summary(batch_id, requester)
            # A header without all its items (a partial create) is repaired by creating again.
            if sum(summary["counts"].values()) == existing["file_count"]:
                self._start(batch_id, resume=False)
                return {
                    **summary,
                    "items": self.uploads.repository.items(batch_id, -1, self.uploads.max_files),
                    "skipped_files": existing.get("skipped_files", 0),
                }
        # Only a name from the server's own listing is accepted; the browser never supplies a path.
        if folder not in {entry["name"] for entry in self.folders()["folders"]}:
            raise DocumentServiceError(
                "IMPORT_FOLDER_NOT_FOUND", "Choose a folder from the import volume.", 404
            )
        limit = self.uploads.max_files
        files: list[dict[str, Any]] = []
        skipped = 0
        try:
            for entry in self.source.list_files(folder):
                if PurePosixPath(entry.relative_path).suffix.lower() != ".pdf":
                    skipped += 1
                    continue
                files.append(
                    {
                        "client_file_id": client_file_id(entry.relative_path),
                        "name": PurePosixPath(entry.relative_path).name,
                        "size": entry.size,
                        "last_modified": None,
                        "relative_path": entry.relative_path,
                    }
                )
                if len(files) > limit:
                    break
        except DocumentServiceError:
            raise
        except Exception as error:
            raise DocumentServiceError(
                "IMPORT_VOLUME_UNAVAILABLE", "The import folder could not be listed.", 503
            ) from error
        if not files:
            raise DocumentServiceError(
                "IMPORT_FOLDER_EMPTY", "The folder contains no PDF files.", 422
            )
        if len(files) > limit:
            raise DocumentServiceError(
                "TOO_MANY_FILES",
                f"The folder has more than {limit:,} PDFs. Split it into smaller folders.",
                422,
            )
        files.sort(key=lambda item: item["relative_path"])
        created = self.uploads.create(
            requester,
            request_id,
            case_id,
            files,
            {"source": "folder", "folder": folder, "skipped_files": skipped},
        )
        self._start(created["batch_id"], resume=False)
        return {**created, "skipped_files": skipped}

    def resume(self, batch_id: str, requester: str) -> dict[str, Any]:
        header = self.uploads.authorize(batch_id, requester)
        if header.get("source") != "folder":
            raise DocumentServiceError("BATCH_NOT_FOUND", "Folder import not found.", 404)
        self._start(batch_id, resume=True)
        return self.uploads.summary(batch_id, requester)

    def _start(self, batch_id: str, resume: bool) -> None:
        try:
            self.start_run(batch_id, resume)
        except Exception as error:
            # The batch is durable; "Retry unfinished files" starts another run.
            raise DocumentServiceError(
                "IMPORT_START_FAILED",
                "The import was saved but could not start. Use Retry to start it.",
                503,
            ) from error

    # Job side ---------------------------------------------------------------------------------

    def run(self, batch_id: str) -> dict[str, int]:
        """Register every unfinished item of one folder batch. Safe to repeat: finished items are
        skipped and item claims stop two runs from registering the same file. Import files are
        never deleted (see ImportSource)."""
        header = self.uploads.repository.header(batch_id)
        if header is None or header.get("source") != "folder":
            raise ValueError(f"{batch_id} is not a folder import batch")
        items = self.uploads.repository.items(batch_id, -1, self.uploads.max_files)
        anyio.run(self._run_items, header, items)
        return self.uploads.repository.counts(batch_id)

    async def _run_items(self, header: dict[str, Any], items: list[dict[str, Any]]) -> None:
        limiter = anyio.CapacityLimiter(self.concurrency)

        async def bounded(item: dict[str, Any]) -> None:
            async with limiter:
                try:
                    await self.import_item(header, item)
                except Exception:
                    # One file's unexpected failure must not stop the rest of the batch.
                    logger.exception("Folder import item %s failed", item["client_file_id"])

        async with anyio.create_task_group() as group:
            for item in items:
                group.start_soon(bounded, item)

    async def import_item(self, header: dict[str, Any], item: dict[str, Any]) -> None:
        folder = header["folder"]
        relative_path = item["relative_path"]
        if item["state"] in TERMINAL_UPLOAD_STATES:
            return
        if item["state"] == "FAILED" and not item["retryable"]:
            return
        if item["attempts"] >= self.uploads.max_attempts:
            return
        try:
            stream, size = await anyio.to_thread.run_sync(self.source.open, folder, relative_path)
        except FileNotFoundError:
            await anyio.to_thread.run_sync(
                self._fail,
                item,
                "IMPORT_FILE_MISSING",
                "The file is no longer in the import folder. Copy it back, then retry.",
                True,
            )
            return
        upload = UploadFile(
            file=stream,
            # Without a reported length, staging still enforces the configured size limit.
            size=item["size"] if size is None else size,
            filename=item["name"],
            headers=Headers({"content-type": "application/pdf"}),
        )
        try:
            await self.uploads.upload(
                header["batch_id"], item["client_file_id"], header["requester"], upload
            )
        except DocumentServiceError as error:
            if error.code in {"FILE_MANIFEST_MISMATCH", "FILE_CONTENT_MISMATCH"}:
                # Both are raised before any claim, so record them here.
                await anyio.to_thread.run_sync(
                    self._fail,
                    item,
                    "IMPORT_FILE_CHANGED",
                    "The file changed after the import started. Start a new import for it.",
                    False,
                )
            # Everything else is already recorded by the upload contract (FAILED with its reason),
            # or another run holds the item (UPLOAD_BUSY) and will record its own outcome.
            return
        finally:
            stream.close()

    def _fail(self, item: dict[str, Any], code: str, message: str, retryable: bool) -> None:
        if item["state"] == "UPLOADING" and (item["lease_expires_at"] or "") > now_iso():
            return  # Another run owns it.
        with suppress(DocumentServiceError):  # Lost a race: the other outcome stands.
            self.uploads.transition(
                item,
                state="FAILED",
                attempts=item["attempts"] + 1,
                lease_owner=None,
                lease_expires_at=None,
                error_code=code,
                error_message=message,
                retryable=retryable,
            )


def background_runner(run: Callable[[str], object]) -> Callable[[str, bool], object]:
    """Local mock mode: run the import in a thread instead of a Databricks Job."""

    def start(batch_id: str, resume: bool) -> None:
        del resume
        threading.Thread(target=run, args=(batch_id,), daemon=True).start()

    return start


class DatabricksImportJobRunner:
    def __init__(self, client: WorkspaceClient, job_id: int) -> None:
        self._client = client
        self._job_id = job_id

    def __call__(self, batch_id: str, resume: bool) -> None:
        import time

        # A replayed start request reuses its token, so it cannot start a second run; a resume
        # may start one run per minute. Two runs on one batch are safe (item claims), just wasteful.
        token = f"import-{batch_id}" + (f"-{int(time.time() // 60)}" if resume else "")
        self._client.jobs.run_now(
            self._job_id, job_parameters={"batch_id": batch_id}, idempotency_token=token
        )
