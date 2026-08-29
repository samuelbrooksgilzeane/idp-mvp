import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

function renderPage() {
  return render(
    <MemoryRouter>
      <DocumentsPage
        documents={documents}
        loading={false}
        caseIds={["CASE-A"]}
        selectedCaseId={null}
        onCaseChanged={vi.fn()}
        onDocumentsChanged={vi.fn()}
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
});
