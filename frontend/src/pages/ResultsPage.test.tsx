import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResultsPage } from "./ResultsPage";

const rows = [
  {
    extraction_run_id: "run-a",
    document_id: "doc-1",
    document_name: "invoice-a.pdf",
    case_id: "CASE-A",
    schema_id: "invoice",
    schema_version: 4,
    schema_display_name: "Invoice v4",
    status: "EXTRACTED",
    started_at: "2026-08-30T10:42:00Z",
    completed_at: "2026-08-30T10:43:00Z",
    is_latest: true,
    records_count: 3,
    issues_count: 0,
  },
  {
    extraction_run_id: "run-b",
    document_id: "doc-1",
    document_name: "invoice-a.pdf",
    case_id: "CASE-A",
    schema_id: "invoice",
    schema_version: 3,
    schema_display_name: "Invoice v3",
    status: "EXTRACTED",
    started_at: "2026-08-29T15:08:00Z",
    completed_at: "2026-08-29T15:09:00Z",
    is_latest: false,
    records_count: 3,
    issues_count: 5,
  },
];

function bySchemaCellText(): string[] {
  return screen
    .getAllByRole("row")
    .slice(1)
    .map((row) => (row as HTMLTableRowElement).cells[2].textContent ?? "");
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ResultsPage", () => {
  it("lists extraction runs, links to the detail page, and defaults to latest-only", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => rows }));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/results"]}>
        <ResultsPage caseIds={["CASE-A", "CASE-B"]} />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("link", { name: "invoice-a.pdf" })).toHaveAttribute(
      "href",
      "/results/run-a",
    );
    expect(screen.getByText("No issues")).toBeInTheDocument();
    // The older run for the same document is filtered out by "Latest runs only" (on by default).
    expect(bySchemaCellText()).toEqual(["Invoice v4 · v4"]);

    fireEvent.click(screen.getByLabelText("Latest runs only"));
    await waitFor(() => expect(bySchemaCellText()).toContain("Invoice v3 · v3"));
    expect(screen.getByText("5 issues")).toBeInTheDocument();
  });

  it("shows a recoverable error state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, json: async () => ({}) })));

    render(
      <MemoryRouter initialEntries={["/results"]}>
        <ResultsPage caseIds={[]} />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Results unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh extraction runs" })).toBeInTheDocument();
  });

  it("warns before exporting two runs for the same document and schema, offering the latest", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/extractions") return { ok: true, json: async () => rows };
      if (input === "/api/exports" && init) {
        return {
          ok: true,
          headers: new Headers({ "Content-Disposition": 'attachment; filename="out.xlsx"' }),
          blob: async () => new Blob(["x"]),
        };
      }
      return { ok: false, json: async () => ({}) };
    }));
    // URL.createObjectURL/revokeObjectURL are not implemented in jsdom.
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:mock"), revokeObjectURL: vi.fn() });

    render(
      <MemoryRouter initialEntries={["/results"]}>
        <ResultsPage caseIds={["CASE-A"]} />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByLabelText("Latest runs only"));
    await waitFor(() => expect(bySchemaCellText()).toContain("Invoice v3 · v3"));

    const checkboxes = screen.getAllByRole("checkbox", { name: /Select run for invoice-a.pdf/ });
    checkboxes.forEach((checkbox) => fireEvent.click(checkbox));

    fireEvent.click(screen.getByRole("button", { name: /Export selected/ }));

    expect(
      await screen.findByText(
        (_, element) => element?.textContent === "Two extraction runs for invoice-a.pdf are selected.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use latest selected run" }));

    await waitFor(() =>
      expect(
        screen.queryByText(
          (_, element) => element?.textContent === "Two extraction runs for invoice-a.pdf are selected.",
        ),
      ).not.toBeInTheDocument(),
    );
  });
});
