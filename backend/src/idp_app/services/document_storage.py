import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, Protocol

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import ResourceAlreadyExists


class DocumentStorage(Protocol):
    def list_source_paths(self) -> Iterator[str]: ...

    def store(self, object_name: str, contents: BinaryIO) -> str: ...

    def verify_existing(self, object_name: str, content_hash: str, size: int) -> str: ...

    def delete(self, object_name: str) -> None: ...


class LocalVolumeStorage:
    def __init__(self, root: Path) -> None:
        self._incoming = root / "source_volume" / "incoming"

    def list_source_paths(self) -> Iterator[str]:
        if self._incoming.exists():
            for path in self._incoming.iterdir():
                if path.is_file():
                    yield path.as_posix()

    def store(self, object_name: str, contents: BinaryIO) -> str:
        if Path(object_name).name != object_name:
            raise ValueError("Storage object name must not contain a path")

        self._incoming.mkdir(parents=True, exist_ok=True)
        destination = self._incoming / object_name
        contents.seek(0)
        with destination.open("xb") as target:
            while chunk := contents.read(1024 * 1024):
                target.write(chunk)
        return destination.as_posix()

    def verify_existing(self, object_name: str, content_hash: str, size: int) -> str:
        if Path(object_name).name != object_name:
            raise ValueError("Storage object name must not contain a path")
        path = self._incoming / object_name
        with path.open("rb") as contents:
            _verify(contents, content_hash, size)
        return path.as_posix()

    def delete(self, object_name: str) -> None:
        if Path(object_name).name != object_name:
            raise ValueError("Storage object name must not contain a path")
        (self._incoming / object_name).unlink(missing_ok=True)


class DatabricksVolumeStorage:
    def __init__(
        self,
        client: WorkspaceClient,
        catalog: str,
        project_schema: str,
        source_volume_name: str,
    ) -> None:
        self._client = client
        self._incoming = f"/Volumes/{catalog}/{project_schema}/{source_volume_name}/incoming"

    def list_source_paths(self) -> Iterator[str]:
        for entry in self._client.files.list_directory_contents(self._incoming, page_size=100):
            if not entry.is_directory and entry.path:
                yield entry.path

    def store(self, object_name: str, contents: BinaryIO) -> str:
        if Path(object_name).name != object_name:
            raise ValueError("Storage object name must not contain a path")

        destination = f"{self._incoming}/{object_name}"
        contents.seek(0)
        self._client.files.create_directory(self._incoming)
        try:
            self._client.files.upload(destination, contents, overwrite=False)
        except ResourceAlreadyExists as error:
            raise FileExistsError(destination) from error
        return destination

    def verify_existing(self, object_name: str, content_hash: str, size: int) -> str:
        if Path(object_name).name != object_name:
            raise ValueError("Storage object name must not contain a path")
        path = f"{self._incoming}/{object_name}"
        response = self._client.files.download(path)
        if response.contents is None:
            raise ValueError("Source PDF has no readable contents")
        try:
            _verify(response.contents, content_hash, size)
        finally:
            response.contents.close()
        return path

    def delete(self, object_name: str) -> None:
        if Path(object_name).name != object_name:
            raise ValueError("Storage object name must not contain a path")
        self._client.files.delete(f"{self._incoming}/{object_name}")


def _verify(contents: BinaryIO, content_hash: str, expected_size: int) -> None:
    digest = hashlib.sha256()
    size = 0
    while chunk := contents.read(1024 * 1024):
        size += len(chunk)
        if size > expected_size:
            raise ValueError("Existing source size differs")
        digest.update(chunk)
    if size != expected_size or digest.hexdigest() != content_hash:
        raise ValueError("Existing source content differs")
