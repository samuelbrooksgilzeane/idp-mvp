from __future__ import annotations

import json
import mimetypes
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Protocol

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from starlette.concurrency import run_in_threadpool

from idp_app.services.document_models import ParseRunRecord
from idp_app.services.document_registry import DocumentRegistry
from idp_app.services.documents import DocumentServiceError
from idp_app.services.parse_runs import ParseRunRepository


@dataclass(frozen=True)
class BoundingBox:
    page_id: int
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class ParsedElement:
    element_id: int
    element_type: str
    content: str
    confidence: float | None
    description: str | None
    boxes: list[BoundingBox]


@dataclass(frozen=True)
class ParsedPage:
    page_id: int
    page_number: int
    element_count: int
    element_types: list[str]
    image_path: str


@dataclass(frozen=True)
class PageImage:
    contents: BinaryIO
    media_type: str
    content_length: int | None


class PageImageStorage(Protocol):
    def open(self, image_path: str) -> PageImage: ...


class LocalPageImageStorage:
    def __init__(self, trusted_root: Path) -> None:
        self._trusted_root = trusted_root.resolve()

    def open(self, image_path: str) -> PageImage:
        path = Path(image_path).resolve()
        if not path.is_relative_to(self._trusted_root):
            raise ValueError("Page image is outside the trusted artifacts directory")
        if not path.is_file():
            raise FileNotFoundError(image_path)
        return PageImage(
            contents=path.open("rb"),
            media_type=_media_type(image_path),
            content_length=path.stat().st_size,
        )


class DatabricksPageImageStorage:
    def __init__(
        self,
        client: WorkspaceClient,
        catalog: str,
        project_schema: str,
        artifacts_volume_name: str,
    ) -> None:
        self._client = client
        self._trusted_root = PurePosixPath(
            f"/Volumes/{catalog}/{project_schema}/{artifacts_volume_name}/page_images"
        )

    def open(self, image_path: str) -> PageImage:
        path = PurePosixPath(image_path)
        if ".." in path.parts or not path.is_relative_to(self._trusted_root):
            raise ValueError("Page image is outside the trusted artifacts volume")
        response = self._client.files.download(image_path)
        if response.contents is None:
            raise FileNotFoundError(image_path)
        return PageImage(
            contents=response.contents,
            media_type=_media_type(image_path),
            content_length=response.content_length,
        )


class ViewerService:
    def __init__(
        self,
        documents: DocumentRegistry,
        parse_runs: ParseRunRepository,
        images: PageImageStorage,
        projections: Any = None,
    ) -> None:
        self._documents = documents
        self._parse_runs = parse_runs
        self._images = images
        self._projections = projections
        self._fallback: OrderedDict[str, tuple[float, int, Any]] = OrderedDict()

    async def _page_data(self, run: ParseRunRecord, page_id: int | None = None):
        if self._projections is not None:
            manifest = await run_in_threadpool(self._projections.manifest, run.parse_run_id)
            if manifest is not None:
                if manifest["document_id"] != run.document_id:
                    raise DocumentServiceError(
                        "PROJECTION_INVALID", "Projection identity mismatch.", 409
                    )
                pages = [ParsedPage(**page) for page in manifest["pages"]]
                elements = []
                if page_id is not None:
                    if page_id not in {p.page_id for p in pages}:
                        raise DocumentServiceError("PAGE_NOT_FOUND", "Parsed page not found.", 404)
                    raw = await run_in_threadpool(self._projections.page, run.parse_run_id, page_id)
                    elements = [
                        ParsedElement(**{**e, "boxes": [BoundingBox(**box) for box in e["boxes"]]})
                        for e in raw
                    ]
                return pages, elements
        from idp_app.services.viewer_projection import project

        now = time.monotonic()
        cached = self._fallback.pop(run.parse_run_id, None)
        if cached is None or cached[0] < now:
            retained = (
                run
                if run.parsed is not None
                else await run_in_threadpool(self._parse_runs.get, run.parse_run_id)
            )
            if retained is None or retained.parsed is None:
                raise DocumentServiceError(
                    "PARSE_RUN_NOT_FOUND", "Retained parse unavailable.", 404
                )
            size = len(json.dumps(retained.parsed).encode())
            data = await run_in_threadpool(project, retained)
            cached = (now + 60, size, data)
        if cached[1] <= 16_000_000:
            self._fallback[run.parse_run_id] = cached
            while (
                len(self._fallback) > 4 or sum(v[1] for v in self._fallback.values()) > 16_000_000
            ):
                self._fallback.popitem(last=False)
        pages, elements = cached[2]
        if page_id is not None and page_id not in elements:
            raise DocumentServiceError("PAGE_NOT_FOUND", "Parsed page not found.", 404)
        return pages, elements.get(page_id, [])

    async def metadata(self, document_id: str, parse_run_id: str | None = None):
        run = await self._successful_run(document_id, parse_run_id)
        pages, _ = await self._page_data(run)
        return run.parse_run_id, pages

    async def list_pages(
        self, document_id: str, parse_run_id: str | None = None
    ) -> list[ParsedPage]:
        _, pages = await self.metadata(document_id, parse_run_id)
        return pages

    async def list_elements(
        self,
        document_id: str,
        page_id: int,
        element_type: str | None = None,
        parse_run_id: str | None = None,
    ) -> list[ParsedElement]:
        run = await self._successful_run(document_id, parse_run_id)
        _, elements = await self._page_data(run, page_id)
        requested = element_type.strip().lower() if element_type else None
        return [e for e in elements if not requested or e.element_type == requested]

    async def open_page_image(
        self, document_id: str, page_id: int, parse_run_id: str | None = None
    ) -> PageImage:
        run = await self._successful_run(document_id, parse_run_id)
        pages, _ = await self._page_data(run)
        page = next((item for item in pages if item.page_id == page_id), None)
        if page is None:
            raise DocumentServiceError("PAGE_NOT_FOUND", "Parsed page not found.", 404)

        image_path = page.image_path
        root = PurePosixPath(run.page_image_root)
        candidate = PurePosixPath(image_path)
        if ".." in candidate.parts or candidate == root or not candidate.is_relative_to(root):
            raise DocumentServiceError(
                "PAGE_IMAGE_INVALID",
                "The parsed page image reference is invalid.",
                409,
            )
        try:
            return await run_in_threadpool(self._images.open, image_path)
        except (FileNotFoundError, NotFound) as error:
            raise DocumentServiceError(
                "PAGE_IMAGE_MISSING",
                "The rendered page image is unavailable.",
                404,
            ) from error
        except ValueError as error:
            raise DocumentServiceError(
                "PAGE_IMAGE_INVALID",
                "The parsed page image reference is invalid.",
                409,
            ) from error
        except Exception as error:
            raise DocumentServiceError(
                "PAGE_IMAGE_READ_FAILED",
                "The rendered page image could not be read.",
                502,
            ) from error

    async def _latest_successful(self, document_id: str) -> ParseRunRecord:
        document = await run_in_threadpool(self._documents.get, document_id)
        if document is None:
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
        run = await run_in_threadpool(self._parse_runs.latest_successful, document_id)
        if run is None or run.parsed is None:
            raise DocumentServiceError(
                "DOCUMENT_NOT_PARSED",
                "The document does not have a successful parse to inspect.",
                409,
            )
        return run

    async def _successful_run(self, document_id: str, parse_run_id: str | None) -> ParseRunRecord:
        document = await run_in_threadpool(self._documents.get, document_id)
        if document is None:
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
        if parse_run_id is None:
            references = await run_in_threadpool(
                self._parse_runs.latest_successful_references, [document_id]
            )
            reference = references.get(document_id)
            if reference is None:
                raise DocumentServiceError(
                    "DOCUMENT_NOT_PARSED", "No successful parse is available.", 409
                )
            parse_run_id = reference.parse_run_id
        reader = getattr(self._parse_runs, "metadata", self._parse_runs.get)
        run = await run_in_threadpool(reader, parse_run_id)
        if run is None or run.document_id != document_id:
            raise DocumentServiceError("PARSE_RUN_NOT_FOUND", "Parse run not found.", 404)
        if run.status != "SUCCESS":
            raise DocumentServiceError(
                "DOCUMENT_NOT_PARSED", "The requested parse is unavailable.", 409
            )
        return run


def _parsed_pages(parsed: dict[str, Any] | None) -> list[tuple[int, str]]:
    document = parsed.get("document") if isinstance(parsed, dict) else None
    raw_pages = document.get("pages") if isinstance(document, dict) else None
    if not isinstance(raw_pages, list) or not raw_pages:
        raise DocumentServiceError(
            "PARSE_PAGES_UNAVAILABLE",
            "The successful parse does not contain inspectable pages.",
            409,
        )

    pages: list[tuple[int, str]] = []
    seen: set[int] = set()
    for raw_page in raw_pages:
        if not isinstance(raw_page, dict):
            continue
        page_id = _integer(raw_page.get("id"))
        image_uri = raw_page.get("image_uri")
        if page_id is None or page_id in seen or not isinstance(image_uri, str):
            continue
        if not image_uri.strip():
            continue
        seen.add(page_id)
        pages.append((page_id, image_uri))
    if not pages:
        raise DocumentServiceError(
            "PARSE_PAGES_UNAVAILABLE",
            "The successful parse does not contain inspectable pages.",
            409,
        )
    return pages


def _parsed_elements(parsed: dict[str, Any] | None) -> list[ParsedElement]:
    document = parsed.get("document") if isinstance(parsed, dict) else None
    raw_elements = document.get("elements") if isinstance(document, dict) else None
    if not isinstance(raw_elements, list):
        return []

    elements: list[ParsedElement] = []
    for index, raw_element in enumerate(raw_elements):
        if not isinstance(raw_element, dict):
            continue
        element_id = _integer(raw_element.get("id"))
        element_type = raw_element.get("type")
        if element_id is None:
            element_id = index
        if not isinstance(element_type, str) or not element_type.strip():
            element_type = "other"
        boxes: list[BoundingBox] = []
        raw_boxes = raw_element.get("bbox")
        if isinstance(raw_boxes, list):
            for raw_box in raw_boxes:
                box = normalise_box(raw_box)
                if box is not None:
                    boxes.append(box)
        confidence = _number(raw_element.get("confidence"))
        content = raw_element.get("content")
        description = raw_element.get("description")
        elements.append(
            ParsedElement(
                element_id=element_id,
                element_type=element_type.strip().lower(),
                content=content if isinstance(content, str) else "",
                confidence=confidence,
                description=description if isinstance(description, str) else None,
                boxes=boxes,
            )
        )
    return elements


def normalise_box(raw_box: object) -> BoundingBox | None:
    if not isinstance(raw_box, dict):
        return None
    page_id = _integer(raw_box.get("page_id"))
    raw_coordinates = raw_box.get("coord")
    if page_id is None or not isinstance(raw_coordinates, list):
        return None
    coordinates = [_number(value) for value in raw_coordinates]
    if any(value is None for value in coordinates):
        return None
    values = [float(value) for value in coordinates if value is not None]
    if len(values) == 4:
        x1, y1, x2, y2 = values
    elif len(values) >= 8 and len(values) % 2 == 0:
        x_values = values[0::2]
        y_values = values[1::2]
        x1, x2 = min(x_values), max(x_values)
        y1, y2 = min(y_values), max(y_values)
    else:
        return None
    if min(x1, y1) < 0 or x2 <= x1 or y2 <= y1:
        return None
    return BoundingBox(page_id=page_id, x=x1, y=y1, width=x2 - x1, height=y2 - y1)


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, int | float) else None


def _media_type(path: str) -> str:
    guessed, _ = mimetypes.guess_type(path)
    if guessed not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError("Unsupported rendered page image type")
    return guessed
