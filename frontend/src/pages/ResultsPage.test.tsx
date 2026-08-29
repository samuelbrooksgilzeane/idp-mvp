import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResultsPage } from "./ResultsPage";

const rows = [{
  document_id: "doc-1",
  file_name: "invoice-a.pdf",
  case_id: "CASE-A",
  invoice_number: "INV-A",
  invoice_date: "2026-08-28",
  seller_name: "Seller A",
  currency: "GBP",
  line_item_count: 2,
  line_items_sum: "80.00",
  total_amount: "75.00",
  reconciliation_delta: "0.00",
  document_status: "VALIDATED_PASS",
}];

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ResultsPage", () => {
  it("filters the summary and exports the same case scope", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => rows }));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/results"]}>
        <ResultsPage caseIds={["CASE-A", "CASE-B"]} />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("link", { name: "INV-A" })).toHaveAttribute(
      "href",
      "/documents/doc-1",
    );
    expect(screen.getByText("Seller A")).toBeInTheDocument();
    expect(screen.getByText(/validated pass/i)).toBeInTheDocument();
    expect(screen.getAllByText("2").length).toBeGreaterThan(0);
    expect(screen.queryByText(/typed projection/i)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Export XLSX/ })).toHaveAttribute(
      "href",
      "/api/exports/invoices.xlsx",
    );

    fireEvent.change(screen.getByLabelText("Case"), { target: { value: "CASE-B" } });
    await waitFor(() =>
      expect(fetchMock).toHaveBeenLastCalledWith(
        "/api/results/invoices?case_id=CASE-B",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
    expect(screen.getByRole("link", { name: /Export XLSX/ })).toHaveAttribute(
      "href",
      "/api/exports/invoices.xlsx?case_id=CASE-B",
    );
  });

  it("distinguishes an empty filtered case from a loading state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => [] })));

    render(
      <MemoryRouter initialEntries={["/results?case_id=CASE-B"]}>
        <ResultsPage caseIds={["CASE-A", "CASE-B"]} />
      </MemoryRouter>,
    );

    expect(await screen.findByText("No extracted invoices")).toBeInTheDocument();
    expect(screen.getByText("This case has no successful invoice extractions yet."))
      .toBeInTheDocument();
    expect(screen.queryByText("Loading invoice results...")).not.toBeInTheDocument();
  });

  it("shows a recoverable error state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, json: async () => ({}) })));

    render(
      <MemoryRouter initialEntries={["/results"]}>
        <ResultsPage caseIds={[]} />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Results unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh invoice results" })).toBeInTheDocument();
  });
});
