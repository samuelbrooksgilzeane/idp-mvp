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
  it("paginates extraction runs in groups of ten", async () => {
    const manyRows = Array.from({ length: 12 }, (_, index) => ({
      ...rows[0],
      extraction_run_id: `run-${index + 1}`,
      document_id: `doc-${index + 1}`,
      document_name: `invoice-${index + 1}.pdf`,
    }));
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      json: async () => ({ items: manyRows, next_cursor: null }),
    })));

    render(<MemoryRouter><ResultsPage /></MemoryRouter>);

    expect(await screen.findAllByRole("link", { name: /invoice-\d+\.pdf/ })).toHaveLength(10);
    fireEvent.click(screen.getByRole("button", { name: "Page 2" }));
    expect(screen.getAllByRole("link", { name: /invoice-\d+\.pdf/ })).toHaveLength(2);
    expect(screen.getByText("11–12 of 12 runs")).toBeInTheDocument();
  });

  it("lists extraction runs, links to the detail page, and defaults to latest-only", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(input.toString(), "http://idp.test");
      const pageRows = url.searchParams.get("latest_only") === "false" ? rows : [rows[0]];
      return { ok: true, json: async () => ({ items: pageRows, next_cursor: null }) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/results"]}>
        <ResultsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("link", { name: "invoice-a.pdf" })).toHaveAttribute(
      "href",
      "/results/run-a",
    );
    // The older run for the same document is filtered out by "Latest runs only" (on by default).
    expect(bySchemaCellText()).toEqual(["Invoice v4 · v4"]);

    fireEvent.click(screen.getByLabelText("Latest runs only"));
    await waitFor(() => expect(bySchemaCellText()).toContain("Invoice v3 · v3"));
  });

  it("shows a recoverable error state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, json: async () => ({}) })));

    render(
      <MemoryRouter initialEntries={["/results"]}>
        <ResultsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Results unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh extraction runs" })).toBeInTheDocument();
  });

  it("prefetches a completed run when its detail link is previewed", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = input.toString();
      if (url.startsWith("/api/extractions?")) {
        return { ok: true, json: async () => ({ items: [rows[0]], next_cursor: null }) };
      }
      if (url === "/api/extractions/run-a/review") {
        return { ok: true, json: async () => ({}) };
      }
      return { ok: false, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/results"]}>
        <ResultsPage />
      </MemoryRouter>,
    );

    fireEvent.pointerEnter(await screen.findByRole("link", { name: "invoice-a.pdf" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/extractions/run-a/review", undefined),
    );
  });

  it("warns before exporting two runs for the same document and schema, offering the latest", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      if (input.startsWith("/api/extractions?")) {
        return { ok: true, json: async () => ({ items: rows, next_cursor: null }) };
      }
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
        <ResultsPage />
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
