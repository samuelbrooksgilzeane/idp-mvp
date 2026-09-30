import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DocumentsPage } from "./DocumentsPage";
import type { DocumentRecord } from "../types";

function document(overrides: Partial<DocumentRecord>): DocumentRecord {
  return {
    document_id: "doc-1",
    case_id: "CASE-A",
    template_id: "invoice_v3",
    use_case: "invoice",
    file_name: "invoice.pdf",
    file_size: 1024,
    content_sha256: "hash",
    status: "UPLOADED",
    uploaded_by: "tester",
    uploaded_at: "2026-08-29T09:00:00Z",
    updated_at: "2026-08-29T09:00:00Z",
    ...overrides,
  };
}

const documents = [
  document({ document_id: "doc-1", file_name: "alpha-invoice.pdf", status: "PARSED" }),
  document({ document_id: "doc-2", file_name: "beta-invoice.pdf", status: "EXTRACTED" }),
  document({ document_id: "doc-3", file_name: "alpha-credit-note.pdf", status: "PARSED" }),
];

function renderPage(onDocumentsChanged = vi.fn()) {
  return render(
    <MemoryRouter>
      <DocumentsPage
        documents={documents}
        loading={false}
        caseIds={["CASE-A"]}
        selectedCaseId={null}
        onCaseChanged={vi.fn()}
        onDocumentsChanged={onDocumentsChanged}
      />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("DocumentsPage", () => {
  it("renders one server page and requests the next page", () => {
    const onNext = vi.fn();
    render(<MemoryRouter><DocumentsPage documents={documents} loading={false}
      caseIds={[]} selectedCaseId={null} onCaseChanged={vi.fn()}
      onDocumentsChanged={vi.fn()} hasNext onNext={onNext} /></MemoryRouter>);
    expect(screen.getAllByRole("button", { name: /alpha.*pdf|beta.*pdf/ }).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(onNext).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
  });

  it("deletes a confirmed document and refreshes the registry", async () => {
    const onDocumentsChanged = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = vi.fn(async () => ({ ok: true, status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    renderPage(onDocumentsChanged);

    fireEvent.click(screen.getByRole("button", { name: "Delete alpha-invoice.pdf" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/documents/doc-1", { method: "DELETE" }));
    expect(await screen.findByText("alpha-invoice.pdf deleted. Extraction results were kept.")).toBeInTheDocument();
    expect(onDocumentsChanged).toHaveBeenCalledTimes(1);
  });

  it("sends status and debounced filename filters to the server owner", async () => {
    const onSearchChanged = vi.fn();
    const onStatusChanged = vi.fn();
    render(<MemoryRouter><DocumentsPage documents={documents} loading={false}
      caseIds={[]} selectedCaseId={null} onCaseChanged={vi.fn()}
      onDocumentsChanged={vi.fn()} onSearchChanged={onSearchChanged}
      onStatusChanged={onStatusChanged} /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "PARSED" } });
    expect(onStatusChanged).toHaveBeenCalledWith("PARSED");
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "credit" } });
    expect(onSearchChanged).not.toHaveBeenCalled();
    await waitFor(() => expect(onSearchChanged).toHaveBeenCalledWith("credit"));
    expect(screen.getByRole("option", { name: "Preparation failed" })).toBeInTheDocument();
  });

  it("preserves explicit selection across pages, filters and remounts", () => {
    const props = { loading: false, caseIds: [], selectedCaseId: null,
      onCaseChanged: vi.fn(), onDocumentsChanged: vi.fn() };
    const view = render(<MemoryRouter><DocumentsPage {...props} documents={documents} /></MemoryRouter>);
    fireEvent.click(screen.getByLabelText("Select this page"));
    expect(screen.getByText("3 selected")).toBeInTheDocument();
    view.rerender(<MemoryRouter><DocumentsPage {...props} documents={[]} search="missing" /></MemoryRouter>);
    expect(screen.getByText("3 selected")).toBeInTheDocument();
    view.unmount();
    render(<MemoryRouter><DocumentsPage {...props} documents={documents} /></MemoryRouter>);
    expect(screen.getByText("3 selected")).toBeInTheDocument();
    expect(screen.getByLabelText("Select alpha-invoice.pdf")).toBeChecked();
  });

  it("warms a document's latest extraction review when its detail button is previewed", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = input.toString();
      if (url === "/api/documents/doc-2/extraction-runs") {
        return {
          ok: true,
          json: async () => [{ extraction_run_id: "run-doc-2", status: "EXTRACTED" }],
        };
      }
      if (url === "/api/extractions/run-doc-2/review") {
        return { ok: true, json: async () => ({}) };
      }
      return { ok: false, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    fireEvent.pointerEnter(screen.getByRole("button", { name: "beta-invoice.pdf" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/extractions/run-doc-2/review", undefined),
    );
  });

  it("delegates uploads to the app-owned transfer manager", async () => {
    const start = vi.fn().mockResolvedValue(undefined);
    render(<MemoryRouter><DocumentsPage documents={[]} loading={false} caseIds={[]}
      selectedCaseId={null} onCaseChanged={vi.fn()} onDocumentsChanged={vi.fn()}
      upload={{ batch: null, busy: false, paused: false, error: null, maxFiles: 1000, maxFileBytes: null, parallelTransfers: 3, start,
        retry: vi.fn(), clear: vi.fn(), pause: vi.fn(), refresh: vi.fn() }} /></MemoryRouter>);
    const file = new File(["%PDF-test"], "test.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/Choose PDF files/), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload documents" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith({ files: [file], caseId: "" }));
  });
});
