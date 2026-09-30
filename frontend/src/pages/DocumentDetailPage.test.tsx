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
    fireEvent.click(screen.getByRole("button", { name: "Prepare document" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Preparing" })).toBeDisabled());
    await waitFor(
      () => expect(screen.getByText("Document parsed successfully.")).toBeInTheDocument(),
      { timeout: 1500 },
    );

    // History is a tab now, not an always-visible stack.
    fireEvent.click(screen.getByRole("tab", { name: "History" }));
    expect(await screen.findByText("2db4e559")).toBeInTheDocument();
    expect(screen.getAllByText("SUCCESS").length).toBeGreaterThan(0);
  });

  it("shows the source and the extracted values side by side", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input.toString();
        if (url.endsWith("/parse-runs")) return { ok: true, json: async () => [] };
        if (url.endsWith("/pages")) return { ok: true, status: 200, json: async () => [] };
        if (url.includes("/extraction-runs")) return { ok: true, json: async () => [] };
        if (url.includes("/validation-runs")) return { ok: true, json: async () => [] };
        if (url.includes("/api/schemas")) return { ok: true, json: async () => [] };
        return { ok: true, json: async () => record };
      }),
    );

    renderPage();
    await screen.findByText("invoice-1042.pdf");

    // Extraction is the default, and the source is visible at the same time rather than
    // behind its own tab, so citing a value never navigates away from the values.
    expect(screen.getByRole("tab", { name: "Extraction" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      await screen.findByRole("heading", { name: "Typed fields and source citations" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Parsed page inspection" })).toBeInTheDocument();

    // Validation swaps only the right-hand panel; the source stays put.
    fireEvent.click(screen.getByRole("tab", { name: "Validation" }));
    expect(
      await screen.findByRole("heading", { name: "Explainable checks and exceptions" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Parsed page inspection" })).toBeInTheDocument();

    // History needs the full width, so the source is hidden but stays mounted, keeping its
    // loaded page image and zoom.
    fireEvent.click(screen.getByRole("tab", { name: "History" }));
    expect(
      screen.queryByRole("heading", { name: "Parsed page inspection" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Parsed page inspection", hidden: true }),
    ).toBeInTheDocument();
  });

  it("shows the parse the extraction read before a value is cited, and citing keeps it", async () => {
    const extractedParse = "parse-extracted";
    const extractionRun = {
      extraction_run_id: "13e7ac76-093f-481d-8360-42375bc8bda8",
      document_id: documentId,
      parse_run_id: extractedParse,
      schema_id: "invoice",
      schema_version: 1,
      schema_hash: "b".repeat(64),
      extractor_version: "2.1",
      status: "EXTRACTED",
      error_message: null,
      requested_by: "analyst@example.com",
      job_run_id: 1,
      started_at: "2026-08-29T11:54:20Z",
      completed_at: "2026-08-29T11:55:48Z",
    };
    const review = {
      run: extractionRun,
      document: { ...record, status: "EXTRACTED" },
      schema_id: "invoice",
      schema_version: 1,
      root_mode: "SINGLE_RECORD",
      result: { total: { value: 888.55 } },
      fields: [{
        record_id: "root", schema_path: "total", instance_path: "total", field_name: "total",
        declared_type: "number", value: 888.55, value_string: "888.55", confidence_score: 1,
        citation_ids: [3],
        citations: [{ id: 3, bbox: [{ coord: [893, 1542, 1222, 1579], page_id: 0 }] }],
        validation_status: null, validation_message: null,
      }],
      field_policies: {},
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = input.toString();
      if (url.endsWith("/parse-runs")) return { ok: true, json: async () => [] };
      if (url.includes("/extraction-runs")) return { ok: true, json: async () => [extractionRun] };
      if (url.includes("/review")) return { ok: true, json: async () => review };
      if (url.includes("/viewer")) {
        return { ok: true, status: 200, json: async () => ({
          parse_run_id: extractedParse,
          pages: [{
            page_id: 0, page_number: 1, element_count: 0, element_types: [],
            image_url: `/api/documents/${documentId}/pages/0/image?parse_run_id=${extractedParse}`,
          }],
        }) };
      }
      if (url.includes("/elements")) return { ok: true, status: 200, json: async () => [] };
      if (url.includes("/api/schemas")) return { ok: true, json: async () => [] };
      return { ok: true, json: async () => ({ ...record, status: "EXTRACTED" }) };
    });
    vi.stubGlobal("fetch", fetchMock);
    const viewerRequests = () =>
      fetchMock.mock.calls.map(([url]) => String(url)).filter((url) => url.includes("/viewer"));

    renderPage();
    const value = await screen.findByRole("button", { name: "888.55" });
    // The document's latest parse may be newer than the one the values were read from; only the
    // extraction's own parse is ever requested.
    await waitFor(() =>
      expect(viewerRequests()).toEqual([
        `/api/documents/${documentId}/viewer?parse_run_id=${extractedParse}`,
      ]),
    );

    fireEvent.click(value);
    expect(await screen.findByText(/Highlighting extraction evidence for/)).toBeInTheDocument();
    expect(viewerRequests()).toHaveLength(1);
  });

  it("reports a document that cannot be loaded", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, json: async () => ({}) })));

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Document not found");
  });
});
