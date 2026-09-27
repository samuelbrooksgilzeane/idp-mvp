"""Artifact storage rooted in a trusted project directory; no client-supplied paths."""

from __future__ import annotations

import hashlib
import shutil
from contextlib import suppress
from pathlib import Path
from typing import Any, BinaryIO, cast
from uuid import UUID

from databricks.sdk.errors import NotFound


class ExportArtifacts:
    def __init__(self, root: str, client: Any = None):
        self.root, self.client = root.rstrip("/"), client

    def path(self, identity: str) -> str:
        return f"{self.root}/exports/{UUID(identity)}/artifact"

    def publish(self, identity: str, file: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        with file.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        target = self.path(identity)
        if self.client is None:
            path = Path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            shutil.copyfile(file, temporary)
            temporary.replace(path)
        else:
            self.client.files.create_directory(target.rsplit("/", 1)[0])
            with file.open("rb") as stream:
                self.client.files.upload(target, stream, overwrite=True)
        return file.stat().st_size, digest.hexdigest()

    def open(self, identity: str) -> BinaryIO:
        if self.client is None:
            return Path(self.path(identity)).open("rb")
        response = self.client.files.download(self.path(identity))
        if response.contents is None:
            raise FileNotFoundError("Export artifact is unavailable")
        return cast(BinaryIO, response.contents)

    def delete(self, identity: str) -> None:
        if self.client is None:
            Path(self.path(identity)).unlink(missing_ok=True)
        else:
            with suppress(NotFound):
                self.client.files.delete(self.path(identity))
