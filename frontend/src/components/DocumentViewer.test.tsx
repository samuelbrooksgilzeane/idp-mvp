import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DocumentViewer, type ParsedElement } from "./DocumentViewer";
import { scaleBoundingBox } from "./viewerGeometry";

const documentId = "d0ed9896-da45-560a-8ddb-5b88d20dea1e";
const pages = [
  {
    page_id: 0,
    page_number: 1,
    element_count: 2,
    element_types: ["table", "text"],
    image_url: `/api/documents/${documentId}/pages/0/image`,
  },
  {
    page_id: 1,
    page_number: 2,
    element_count: 1,
    element_types: ["footnote"],
    image_url: `/api/documents/${documentId}/pages/1/image`,
  },
];
const pageOneElements: ParsedElement[] = [
  {
    element_id: 7,
    element_type: "text",
    content: "Invoice number INV-5814",
    confidence: 0.9874,
    description: null,
    boxes: [{ page_id: 0, x: 100, y: 200, width: 400, height: 80 }],
  },
  {
    element_id: 8,
    element_type: "table",
    content: "Line items and totals",
    confidence: 0.9341,
    description: null,
    boxes: [{ page_id: 0, x: 80, y: 520, width: 1260, height: 610 }],
  },
];
const pageTwoElements: ParsedElement[] = [
  {
    element_id: 12,
    element_type: "footnote",
    content: "Payment due within 30 days",
    confidence: 0.9012,
    description: null,
    boxes: [{ page_id: 1, x: 90, y: 1920, width: 820, height: 70 }],
  },
];

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("DocumentViewer", () => {
  it("keeps boxes aligned at multiple rendered image sizes", () => {
    const box = { page_id: 0, x: 160, y: 220, width: 640, height: 440 };

    expect(
      scaleBoundingBox(
        box,
        { width: 1600, height: 2200 },
        { width: 800, height: 1100 },
      ),
    ).toEqual({ page_id: 0, x: 80, y: 110, width: 320, height: 220 });
    expect(
      scaleBoundingBox(
        box,
        { width: 1600, height: 2200 },
        { width: 2000, height: 2750 },
      ),
    ).toEqual({ page_id: 0, x: 200, y: 275, width: 800, height: 550 });
  });

  it("renders labelled overlays, filters types, zooms, and inspects content", async () => {
    vi.stubGlobal("fetch", viewerFetch());
    render(<DocumentViewer documentId={documentId} documentStatus="PARSED" />);

    const image = await screen.findByAltText("Rendered page 1");
    setImageDimensions(image, 1600, 2200, 800, 1100);
    fireEvent.load(image);

    const textBox = await screen.findByRole("button", {
      name: /text 7: Invoice number INV-5814/,
    });
    expect(textBox).toHaveStyle({
      transform: "translate3d(50px, 100px, 0)",
      width: "200px",
      height: "40px",
    });
    expect(screen.getByRole("button", { name: /table 8:/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "table", pressed: true }));
    expect(screen.queryByRole("button", { name: /table 8:/ })).not.toBeInTheDocument();
    fireEvent.click(textBox);
    expect(screen.getByText("98.7%")).toBeInTheDocument();
    expect(screen.getAllByText("Invoice number INV-5814")).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(screen.getByTestId("page-sheet")).toHaveStyle({ width: "125%" });
    expect(screen.getByRole("button", { name: /Reset zoom, currently 125%/ })).toBeInTheDocument();
  });

  it("navigates incrementally and shows image failure without losing metadata", async () => {
    const fetchMock = viewerFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<DocumentViewer documentId={documentId} documentStatus="PARSED" />);

    await screen.findByAltText("Rendered page 1");
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    const secondImage = await screen.findByAltText("Rendered page 2");
    expect(secondImage).toHaveAttribute("src", pages[1].image_url);
    await waitFor(() => expect(screen.getByText("1 elements here")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/documents/${documentId}/elements?page_id=1`,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );

    fireEvent.error(secondImage);
    expect(screen.getByRole("alert")).toHaveTextContent("Page image unavailable");
    expect(screen.getByText("The retained parse metadata is still available.")).toBeInTheDocument();
  });

  it("shows an intentional empty state before parsing", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<DocumentViewer documentId={documentId} documentStatus="UPLOADED" />);

    expect(screen.getByText("Viewer waiting")).toBeInTheDocument();
    expect(screen.getByText(/Parse this document/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

function viewerFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = input.toString();
    if (url.endsWith("/pages")) return { ok: true, status: 200, json: async () => pages };
    if (url.includes("page_id=1")) {
      return { ok: true, status: 200, json: async () => pageTwoElements };
    }
    return { ok: true, status: 200, json: async () => pageOneElements };
  });
}

function setImageDimensions(
  image: HTMLElement,
  naturalWidth: number,
  naturalHeight: number,
  clientWidth: number,
  clientHeight: number,
) {
  Object.defineProperties(image, {
    naturalWidth: { configurable: true, value: naturalWidth },
    naturalHeight: { configurable: true, value: naturalHeight },
    clientWidth: { configurable: true, value: clientWidth },
    clientHeight: { configurable: true, value: clientHeight },
  });
}
