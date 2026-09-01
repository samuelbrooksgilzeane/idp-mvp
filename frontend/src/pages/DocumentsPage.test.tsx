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
  vi.unstubAllGlobals();
});

describe("DocumentsPage", () => {
  it("narrows the registry by status and by file name", () => {
    renderPage();
    expect(screen.getByText("3 documents")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "PARSED" } });
    expect(screen.getByText("2 of 3 documents")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "beta-invoice.pdf" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "credit" } });
    expect(screen.getByText("1 of 3 documents")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "alpha-credit-note.pdf" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "nothing here" } });
    expect(screen.getByText("No matching documents")).toBeInTheDocument();
  });

  it("only offers the statuses the registry actually contains", () => {
    renderPage();
    const options = screen
      .getAllByRole("option")
      .map((option) => option.textContent)
      .filter((label) => label !== "All cases" && label !== "CASE-A");
    expect(options).toEqual(["All statuses", "EXTRACTED", "PARSED"]);
  });

  it("never batches a document the filter has hidden", () => {
    renderPage();
    fireEvent.click(screen.getByLabelText("Select all documents"));
    expect(screen.getByText("3 selected")).toBeInTheDocument();

    // Narrowing the view narrows the batch: the two hidden documents drop out of it.
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "credit" } });
    expect(screen.getByText("1 selected")).toBeInTheDocument();

    // Selecting all while filtered adds only what is visible.
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "" } });
    expect(screen.getByText("3 selected")).toBeInTheDocument();
  });

  it("registers multiple PDFs as bounded one-file requests", async () => {
    const onDocumentsChanged = vi.fn();
    const first = document({ document_id: "uploaded-1", file_name: "first.pdf" });
    const second = document({ document_id: "uploaded-2", file_name: "second.pdf" });
    const uploadResponses = [first, second];
    let uploadIndex = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      void _init;
      if (input.toString().includes("/api/schemas")) {
        return {
          ok: true,
          status: 200,
          text: async () => JSON.stringify([]),
        };
      }

      const uploadedDocument = uploadResponses[uploadIndex++];
      return {
        ok: true,
        status: 201,
        text: async () => JSON.stringify({ documents: [uploadedDocument], errors: [] }),
      };
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage(onDocumentsChanged);

    const files = [
      new File(["%PDF-first"], "first.pdf", { type: "application/pdf" }),
      new File(["%PDF-second"], "second.pdf", { type: "application/pdf" }),
    ];
    fireEvent.change(screen.getByLabelText(/Choose PDF files/i), {
      target: { files },
    });
    fireEvent.click(screen.getByRole("button", { name: "Register documents" }));

    expect(await screen.findByText("2 documents registered.")).toBeInTheDocument();
    const uploadCalls = fetchMock.mock.calls.filter(
      ([input]) => input.toString() === "/api/documents",
    );
    expect(uploadCalls).toHaveLength(2);
    for (const [, init] of uploadCalls) {
      const body = init?.body as FormData;
      expect(body.getAll("files")).toHaveLength(1);
    }
    expect(onDocumentsChanged).toHaveBeenCalledTimes(1);
  });

  it("turns an HTML gateway error into a useful upload message", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 413,
      text: async () => "<!DOCTYPE html><html><body>Request too large</body></html>",
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    const file = new File(["%PDF-large"], "oversized.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/Choose PDF files/i), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Register documents" }));

    await waitFor(() =>
      expect(
        screen.getByText(
          "oversized.pdf: the PDF is too large for the server or app gateway.",
        ),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Unexpected token/)).not.toBeInTheDocument();
  });
});
