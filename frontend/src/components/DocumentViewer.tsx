import { useViewerPage } from "../hooks/useViewerPage";
import {
  ChevronLeft,
  ChevronRight,
  ImageOff,
  Layers3,
  LocateFixed,
  ScanSearch,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";

import type { DocumentStatus } from "../types";
import {
  citationToBox,
  citedElementId,
  scaleBoundingBox,
  type CitationCoordinate,
} from "./viewerGeometry";

export type PageMetadata = {
  page_id: number;
  page_number: number;
  element_count: number;
  element_types: string[];
  image_url: string;
};

export type ElementBox = {
  page_id: number;
  x: number;
  y: number;
  width: number;
  height: number;
};

export type ParsedElement = {
  element_id: number;
  element_type: string;
  content: string;
  confidence: number | null;
  description: string | null;
  boxes: ElementBox[];
};

export type CitationTarget = {
  parseRunId?: string;
  pageId: number;
  fieldLabel: string;
  boxes: CitationCoordinate[];
  nonce: number;
};

type Size = { width: number; height: number };
type LoadedImage = { key: string; state: "ready" | "error"; natural: Size };
const NO_SIZE: Size = { width: 0, height: 0 };
type ViewerState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; pages: PageMetadata[] }
  | { kind: "empty"; message: string }
  | { kind: "error"; message: string };

type DocumentViewerProps = {
  documentId: string;
  documentStatus: DocumentStatus;
  parseRunId?: string;
  /** The page has yet to resolve which parse to show; hold the loading state until it has. */
  sourcePending?: boolean;
  citationTarget?: CitationTarget | null;
};

const zoomLevels = [75, 100, 125, 150, 200];

export function DocumentViewer({
  documentId,
  documentStatus,
  parseRunId,
  sourcePending = false,
  citationTarget,
}: DocumentViewerProps) {
  const [viewer, setViewer] = useState<ViewerState>({ kind: "idle" });
  const [pageIndex, setPageIndex] = useState(0);
  const [zoom, setZoom] = useState(100);
  const [resolvedParseId, setResolvedParseId] = useState(parseRunId);
  const [selectedTypes, setSelectedTypes] = useState<Set<string>>(new Set());
  const [selectedElementId, setSelectedElementId] = useState<number | null>(null);
  // Load state and sizes belong to one image (imageKey below). Keyed, a new page reads as loading
  // by itself; reset in an effect, a load that landed before the effect ran (a cached image) was
  // undone by it and the overlay never appeared.
  const [loaded, setLoaded] = useState<LoadedImage | null>(null);
  const [measured, setMeasured] = useState<{ key: string; size: Size } | null>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  // The parse on screen, so asking for that same parse again (a cited value, a tab switch or a
  // workflow status change) keeps the page rather than reloading it.
  const shownParse = useRef<string | null>(null);

  useEffect(() => {
    if (parseRunId && shownParse.current === `${documentId}:${parseRunId}`) return;
    const controller = new AbortController();
    shownParse.current = null;
    setPageIndex(0);
    setZoom(100);
    setSelectedElementId(null);

    // Workflow status can lag retained parse history (including historical extraction
    // evidence). Let the authenticated viewer endpoint determine availability.
    setViewer({ kind: "loading" });
    // Loading the latest parse while the evidence source is unresolved would draw elements the
    // cited values were not read from, then reload once it resolves.
    if (sourcePending) return () => controller.abort();
    const query = parseRunId ? `?parse_run_id=${encodeURIComponent(parseRunId)}` : "";
    fetch(`/api/documents/${documentId}/viewer${query}`, { signal: controller.signal })
      .then(async (response) => {
        if (controller.signal.aborted) return;
        if (response.status === 409) {
          const payload = (await response.json()) as { error?: { message?: string } };
          setViewer({
            kind: "empty",
            message: payload.error?.message ?? "No successful parse is available yet.",
          });
          return;
        }
        if (!response.ok) throw new Error("Page metadata request failed");
        const payload = (await response.json()) as unknown;
        if (controller.signal.aborted) return;
        const envelope = payload as { parse_run_id?: string; pages?: unknown[] };
        const resolved = envelope.parse_run_id || parseRunId;
        setResolvedParseId(resolved);
        const raw = Array.isArray(payload) ? payload : envelope.pages;
        const pages = Array.isArray(raw) ? raw.filter(isPageMetadata) : [];
        if (pages.length) shownParse.current = `${documentId}:${resolved}`;
        setViewer(
          pages.length
            ? { kind: "ready", pages }
            : { kind: "empty", message: "The parse contains no inspectable pages." },
        );
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted && !(error instanceof DOMException && error.name === "AbortError")) {
          setViewer({ kind: "error", message: "The parsed pages could not be loaded." });
        }
      });
    return () => controller.abort();
  }, [documentId, documentStatus, parseRunId, sourcePending]);

  const currentPage = viewer.kind === "ready" ? viewer.pages[pageIndex] : null;

  const nextPage = viewer.kind === "ready" ? viewer.pages[pageIndex + 1] ?? null : null;
  const imageKey = `${documentId}:${resolvedParseId}:${currentPage?.page_id}`;
  const shown = loaded?.key === imageKey ? loaded : null;
  const imageState = shown?.state ?? "loading";
  const naturalSize = shown?.natural ?? NO_SIZE;
  const renderedSize = measured?.key === imageKey ? measured.size : NO_SIZE;
  const { elements, loading: elementsLoading, error: elementsError } = useViewerPage(
    documentId, resolvedParseId, currentPage, nextPage, imageState === "ready",
  );
  const activeImage = useRef(imageKey);
  activeImage.current = imageKey;
  useEffect(() => {
    setSelectedElementId(null);
    setSelectedTypes(new Set(currentPage?.element_types || []));
  }, [currentPage, imageKey]);

  useLayoutEffect(() => {
    const image = imageRef.current;
    if (!image || imageState !== "ready") return;
    const measure = () => {
      setMeasured({ key: imageKey, size: { width: image.clientWidth, height: image.clientHeight } });
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(image);
    return () => observer.disconnect();
  }, [imageState, imageKey, zoom, currentPage]);

  useEffect(() => {
    if (!citationTarget || viewer.kind !== "ready") return;
    const index = viewer.pages.findIndex((page) => page.page_id === citationTarget.pageId);
    if (index >= 0) setPageIndex(index);
  }, [citationTarget, viewer]);

  const visibleElements = useMemo(
    () => elements.filter((element) => selectedTypes.has(element.element_type)),
    [elements, selectedTypes],
  );
  const citationBoxes = useMemo(
    () =>
      citationTarget && currentPage
        ? citationTarget.boxes.filter((box) => box.page_id === currentPage.page_id)
        : [],
    [citationTarget, currentPage],
  );
  // Select the element each cited value was read from, so it is outlined and the inspector names
  // it and shows its parsed content alongside the highlight.
  useEffect(() => {
    if (!citationTarget) return;
    const regions = citationBoxes
      .map(citationToBox)
      .filter((box): box is ElementBox => box !== null);
    const cited = citedElementId(regions, elements);
    if (cited !== null) setSelectedElementId(cited);
  }, [citationTarget, citationBoxes, elements]);
  const selectedElement =
    elements.find((element) => element.element_id === selectedElementId) ?? null;
  const allTypesSelected =
    currentPage !== null && selectedTypes.size === currentPage.element_types.length;

  function movePage(direction: -1 | 1) {
    if (viewer.kind !== "ready") return;
    setPageIndex((current) =>
      Math.min(Math.max(current + direction, 0), viewer.pages.length - 1),
    );
  }

  function changeZoom(direction: -1 | 1) {
    const currentIndex = zoomLevels.indexOf(zoom);
    const nextIndex = Math.min(
      Math.max(currentIndex + direction, 0),
      zoomLevels.length - 1,
    );
    setZoom(zoomLevels[nextIndex]);
  }

  function toggleType(elementType: string) {
    if (selectedElement?.element_type === elementType) setSelectedElementId(null);
    setSelectedTypes((current) => {
      const next = new Set(current);
      if (next.has(elementType)) next.delete(elementType);
      else next.add(elementType);
      return next;
    });
  }

  function toggleAllTypes() {
    if (!currentPage) return;
    if (allTypesSelected) setSelectedElementId(null);
    setSelectedTypes(
      allTypesSelected ? new Set() : new Set(currentPage.element_types),
    );
  }

  return (
    <section className="document-viewer" aria-labelledby="viewer-title">
      <header className="viewer-heading">
        <div>
          <p className="eyebrow">Visual debugger</p>
          <h3 id="viewer-title">Parsed page inspection</h3>
        </div>
        {viewer.kind === "ready" ? (
          <div className="viewer-summary" aria-label="Page parse summary">
            <span>{viewer.pages.length} {viewer.pages.length === 1 ? "page" : "pages"}</span>
            <span>{currentPage?.element_count ?? 0} elements here</span>
          </div>
        ) : null}
      </header>

      {viewer.kind === "loading" ? <ViewerSkeleton /> : null}
      {viewer.kind === "empty" ? (
        <div className="viewer-message viewer-message-empty">
          <ScanSearch size={24} strokeWidth={1.5} aria-hidden="true" />
          <strong>Viewer waiting</strong>
          <span>{viewer.message}</span>
        </div>
      ) : null}
      {viewer.kind === "error" ? (
        <div className="viewer-message viewer-message-error" role="alert">
          <ImageOff size={24} strokeWidth={1.5} aria-hidden="true" />
          <strong>Viewer unavailable</strong>
          <span>{viewer.message}</span>
        </div>
      ) : null}

      {viewer.kind === "ready" && currentPage ? (
        <>
          <div className="viewer-toolbar" aria-label="Viewer controls">
            <div className="viewer-control-group">
              <button
                className="viewer-icon-button"
                type="button"
                onClick={() => movePage(-1)}
                disabled={pageIndex === 0}
                aria-label="Previous page"
              >
                <ChevronLeft size={17} strokeWidth={1.7} aria-hidden="true" />
              </button>
              <span className="page-position">
                Page <strong>{pageIndex + 1}</strong> / {viewer.pages.length}
              </span>
              <button
                className="viewer-icon-button"
                type="button"
                onClick={() => movePage(1)}
                disabled={pageIndex === viewer.pages.length - 1}
                aria-label="Next page"
              >
                <ChevronRight size={17} strokeWidth={1.7} aria-hidden="true" />
              </button>
            </div>
            <div className="viewer-control-group">
              <button
                className="viewer-icon-button"
                type="button"
                onClick={() => changeZoom(-1)}
                disabled={zoom === zoomLevels[0]}
                aria-label="Zoom out"
              >
                <ZoomOut size={16} strokeWidth={1.7} aria-hidden="true" />
              </button>
              <button
                className="zoom-readout"
                type="button"
                onClick={() => setZoom(100)}
                aria-label={`Reset zoom, currently ${zoom}%`}
              >
                {zoom}%
              </button>
              <button
                className="viewer-icon-button"
                type="button"
                onClick={() => changeZoom(1)}
                disabled={zoom === zoomLevels.at(-1)}
                aria-label="Zoom in"
              >
                <ZoomIn size={16} strokeWidth={1.7} aria-hidden="true" />
              </button>
            </div>
          </div>

          <div className="element-filters" aria-label="Element type filters">
            <button
              type="button"
              className={allTypesSelected ? "active" : undefined}
              aria-pressed={allTypesSelected}
              onClick={toggleAllTypes}
            >
              <Layers3 size={14} strokeWidth={1.7} aria-hidden="true" /> All
            </button>
            {currentPage.element_types.map((elementType) => (
              <button
                type="button"
                key={elementType}
                className={`filter-${typeClass(elementType)} ${selectedTypes.has(elementType) ? "active" : ""}`}
                aria-pressed={selectedTypes.has(elementType)}
                onClick={() => toggleType(elementType)}
              >
                <span aria-hidden="true" />{elementType}
              </button>
            ))}
          </div>

          {citationTarget && citationBoxes.length > 0 ? (
            <div className="citation-legend" role="status">
              <span className="citation-swatch" aria-hidden="true" />
              Highlighting extraction evidence for <strong>{citationTarget.fieldLabel}</strong>
            </div>
          ) : null}

          {elementsError ? <p role="alert">{elementsError}</p> : null}
          <div className="viewer-workspace">
            <div className="page-viewport" aria-busy={imageState === "loading"}>
              {imageState === "loading" ? <div className="page-image-skeleton" /> : null}
              {imageState === "error" ? (
                <div className="page-image-error" role="alert">
                  <ImageOff size={28} strokeWidth={1.5} aria-hidden="true" />
                  <strong>Page image unavailable</strong>
                  <span>The retained parse metadata is still available.</span>
                </div>
              ) : null}
              <div
                className={`page-sheet page-sheet-${imageState}`}
                style={{ width: `${zoom}%` }}
                data-testid="page-sheet"
              >
                <img
                  key={imageKey}
                  ref={imageRef}
                  src={currentPage.image_url}
                  alt={`Rendered page ${currentPage.page_number}`}
                  onLoad={(event) => {
                    if (activeImage.current !== imageKey) return;
                    const image = event.currentTarget;
                    setLoaded({
                      key: imageKey,
                      state: "ready",
                      natural: { width: image.naturalWidth, height: image.naturalHeight },
                    });
                    setMeasured({
                      key: imageKey,
                      size: { width: image.clientWidth, height: image.clientHeight },
                    });
                  }}
                  onError={() => {
                    if (activeImage.current === imageKey) {
                      setLoaded({ key: imageKey, state: "error", natural: NO_SIZE });
                    }
                  }}
                />
                {imageState === "ready" ? (
                  <div className="element-overlay" aria-label="Detected page elements">
                    {visibleElements.flatMap((element) =>
                      element.boxes.map((box, boxIndex) => {
                        const scaled = scaleBoundingBox(box, naturalSize, renderedSize);
                        return (
                          <button
                            type="button"
                            className={`element-box element-${typeClass(element.element_type)} ${selectedElementId === element.element_id ? "selected" : ""}`}
                            style={boxStyle(scaled)}
                            key={`${element.element_id}-${boxIndex}`}
                            aria-label={`${element.element_type} ${element.element_id}: ${element.content || "No extracted text"}`}
                            onClick={() => setSelectedElementId(element.element_id)}
                          >
                            <span>{element.element_type} #{element.element_id}</span>
                          </button>
                        );
                      }),
                    )}
                  </div>
                ) : null}
                {imageState === "ready" && citationTarget && citationBoxes.length > 0 ? (
                  <div
                    className="citation-overlay"
                    aria-label={`Evidence for ${citationTarget.fieldLabel}`}
                  >
                    {citationBoxes.map((citation, index) => {
                      const box = citationToBox(citation);
                      if (box === null) return null;
                      const scaled = scaleBoundingBox(box, naturalSize, renderedSize);
                      return (
                        <div
                          className="citation-box"
                          style={boxStyle(scaled)}
                          key={`citation-${citationTarget.nonce}-${index}`}
                          role="img"
                          aria-label={`Cited evidence for ${citationTarget.fieldLabel} on page ${currentPage.page_number}`}
                        >
                          <span aria-hidden="true">{citationTarget.fieldLabel}</span>
                        </div>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            </div>

            <aside className="element-inspector" aria-label="Selected element details">
              <div className="inspector-heading">
                <LocateFixed size={16} strokeWidth={1.7} aria-hidden="true" />
                <strong>Element inspector</strong>
              </div>
              {elementsLoading ? (
                <div className="inspector-loading"><span /><span /><span /></div>
              ) : selectedElement ? (
                <ElementDetails element={selectedElement} />
              ) : (
                <div className="inspector-empty">
                  <span>Select a labelled region on the page.</span>
                  <small>{visibleElements.length} visible elements</small>
                </div>
              )}
              {!elementsLoading && elements.length > 0 ? (
                <ol className="element-index">
                  {visibleElements.map((element) => (
                    <li key={element.element_id}>
                      <button
                        type="button"
                        className={selectedElementId === element.element_id ? "active" : undefined}
                        onClick={() => setSelectedElementId(element.element_id)}
                      >
                        <span className={`element-swatch element-${typeClass(element.element_type)}`} />
                        <span><strong>{element.element_type} #{element.element_id}</strong><small>{element.content || "No extracted text"}</small></span>
                      </button>
                    </li>
                  ))}
                </ol>
              ) : null}
            </aside>
          </div>
        </>
      ) : null}
    </section>
  );
}

function ElementDetails({ element }: { element: ParsedElement }) {
  return (
    <div className="element-details">
      <div className="element-details-title">
        <span className={`element-swatch element-${typeClass(element.element_type)}`} />
        <strong>{element.element_type} #{element.element_id}</strong>
      </div>
      <dl>
        <div><dt>Confidence</dt><dd>{formatConfidence(element.confidence)}</dd></div>
        <div><dt>Regions</dt><dd>{element.boxes.length}</dd></div>
      </dl>
      <div className="element-copy">
        <span>Extracted content</span>
        <p>{element.content || "No text content was returned for this element."}</p>
      </div>
      {element.description ? (
        <div className="element-copy"><span>Description</span><p>{element.description}</p></div>
      ) : null}
    </div>
  );
}

function ViewerSkeleton() {
  return (
    <div className="viewer-skeleton" aria-label="Loading parsed pages">
      <div className="skeleton-toolbar" />
      <div className="skeleton-page" />
    </div>
  );
}

function boxStyle(box: ElementBox): CSSProperties {
  return {
    transform: `translate3d(${box.x}px, ${box.y}px, 0)`,
    width: `${box.width}px`,
    height: `${box.height}px`,
  };
}

function formatConfidence(confidence: number | null): string {
  if (confidence === null) return "Not reported";
  return `${(confidence * 100).toFixed(1)}%`;
}

function typeClass(elementType: string): string {
  return elementType.toLowerCase().replace(/[^a-z0-9_-]/g, "-");
}

function isPageMetadata(value: unknown): value is PageMetadata {
  if (!value || typeof value !== "object") return false;
  const page = value as Partial<PageMetadata>;
  return (
    typeof page.page_id === "number"
    && typeof page.page_number === "number"
    && typeof page.element_count === "number"
    && Array.isArray(page.element_types)
    && page.element_types.every((item) => typeof item === "string")
    && typeof page.image_url === "string"
    && page.image_url.startsWith("/api/documents/")
  );
}
