from pathlib import Path
from typing import BinaryIO, Protocol

from databricks.sdk import WorkspaceClient


class DocumentStorage(Protocol):
    def store(self, object_name: str, contents: BinaryIO) -> str: ...

    def delete(self, object_name: str) -> None: ...


class LocalVolumeStorage:
    def __init__(self, root: Path) -> None:
        self._incoming = root / "source_volume" / "incoming"

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
        self._incoming = (
            f"/Volumes/{catalog}/{project_schema}/{source_volume_name}/incoming"
        )

    def store(self, object_name: str, contents: BinaryIO) -> str:
        if Path(object_name).name != object_name:
            raise ValueError("Storage object name must not contain a path")

        destination = f"{self._incoming}/{object_name}"
        contents.seek(0)
        self._client.files.create_directory(self._incoming)
        self._client.files.upload(destination, contents, overwrite=False)
        return destination

    def delete(self, object_name: str) -> None:
        if Path(object_name).name != object_name:
            raise ValueError("Storage object name must not contain a path")
        self._client.files.delete(f"{self._incoming}/{object_name}")
