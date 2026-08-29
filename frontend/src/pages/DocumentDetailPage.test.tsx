import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DocumentDetailPage } from "./DocumentDetailPage";

const documentId = "9e4ef80e-fef3-5e13-ae29-f8dc585b15cb";

const record = {
  document_id: documentId,
  case_id: "CASE-1042",
  template_id: "invoice_v1",
  use_case: "invoice",
  file_name: "invoice-1042.pdf",
  file_size: 2048,
  content_sha256: "a".repeat(64),
  status: "UPLOADED",
  uploaded_by: "analyst@example.com",
  uploaded_at: "2026-08-28T09:00:00Z",
  updated_at: "2026-08-28T09:00:00Z",
};

const runningRun = {
  parse_run_id: "2db4e559-76d0-4e9e-a0de-d17e84699fca",
  document_id: documentId,
  parser_version: "2.0",
  status: "RUNNING",
  page_count: null,
  parse_error: null,
  requested_by: "analyst@example.com",
  started_at: "2026-08-28T09:05:00Z",
  completed_at: null,
};
const successfulRun = {
  ...runningRun,
  status: "SUCCESS",
  page_count: 2,
  completed_at: "2026-08-28T09:05:02Z",
};

function renderPage(onChanged = vi.fn()) {
  return render(
    <MemoryRouter initialEntries={[`/documents/${documentId}`]}>
      <Routes>
        <Route
          path="/documents/:documentId"
          element={<DocumentDetailPage onDocumentsChanged={onChanged} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("DocumentDetailPage", () => {
  it("starts parsing, polls the run, and shows immutable history", async () => {
    let parsed = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input.toString();
        if (url.endsWith("/parse") && init?.method === "POST") {
          return { ok: true, json: async () => runningRun };
        }
        if (url.includes("/api/runs/")) {
          parsed = true;
          return { ok: true, json: async () => successfulRun };
        }
        if (url.endsWith("/parse-runs")) {
          return { ok: true, json: async () => (parsed ? [successfulRun] : []) };
        }
        if (url.endsWith("/pages")) return { ok: true, status: 200, json: async () => [] };
        return {
          ok: true,
          json: async () => ({ ...record, status: parsed ? "PARSED" : "UPLOADED" }),
        };
      }),
    );

    renderPage();
    await screen.findByText("invoice-1042.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Parse document" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Parsing" })).toBeDisabled());
    await waitFor(
      () => expect(screen.getByText("Document parsed successfully.")).toBeInTheDocument(),
      { timeout: 1500 },
    );

    // History is a tab now, not an always-visible stack.
    fireEvent.click(screen.getByRole("tab", { name: "History" }));
    expect(await screen.findByText("2db4e559")).toBeInTheDocument();
    expect(screen.getAllByText("SUCCESS").length).toBeGreaterThan(0);
  });

  it("switches sections without unmounting the page viewer", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input.toString();
        if (url.endsWith("/parse-runs")) return { ok: true, json: async () => [] };
        if (url.endsWith("/pages")) return { ok: true, status: 200, json: async () => [] };
        if (url.includes("/extraction-runs")) return { ok: true, json: async () => [] };
        if (url.includes("/api/schemas")) return { ok: true, json: async () => [] };
        return { ok: true, json: async () => record };
      }),
    );

    renderPage();
    await screen.findByText("invoice-1042.pdf");

    expect(screen.getByRole("tab", { name: "Pages" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("tab", { name: "Extraction" }));

    expect(
      await screen.findByRole("heading", { name: "Typed fields and source citations" }),
    ).toBeInTheDocument();
    // The viewer stays mounted so its loaded image and zoom survive a tab switch, but while
    // hidden it is correctly kept out of the accessibility tree.
    expect(
      screen.queryByRole("heading", { name: "Parsed page inspection" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Parsed page inspection", hidden: true }),
    ).toBeInTheDocument();
  });

  it("reports a document that cannot be loaded", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, json: async () => ({}) })));

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Document not found");
  });
});
