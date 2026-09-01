import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResultDetailPage } from "./ResultDetailPage";

const runId = "run-a1b2";
const documentId = "doc-1";

const genericResult = {
  run: {
    extraction_run_id: runId,
    document_id: documentId,
    parse_run_id: "parse-1",
    schema_id: "invoice",
    schema_version: 4,
    schema_hash: "abc123",
    extractor_version: "2.1",
    options: {},
    error_message: null,
    status: "EXTRACTED",
    requested_by: "analyst@example.com",
    job_run_id: null,
    started_at: "2026-08-30T10:42:00Z",
    completed_at: "2026-08-30T10:43:00Z",
  },
  schema_id: "invoice",
  schema_version: 4,
  root_mode: "SINGLE_RECORD",
  result: { total: { value: 114, confidence_score: 0.95, citation_ids: [], citations: [] } },
};

const genericRecords = {
  run: genericResult.run,
  schema_id: "invoice",
  schema_version: 4,
  root_mode: "SINGLE_RECORD",
  records: [{ record_id: "r1", parent_record_id: null, schema_path: "$", instance_path: "$", ordinal: null }],
  fields: [
    {
      record_id: "r1",
      schema_path: "total",
      instance_path: "total",
      field_name: "total",
      declared_type: "number",
      value: 114,
      value_string: "114",
      confidence_score: 0.95,
      citation_ids: [],
      citations: [],
      validation_status: null,
      validation_message: null,
    },
  ],
};

const document = {
  document_id: documentId,
  case_id: "CASE-A",
  template_id: "invoice_v1",
  use_case: "invoice",
  file_name: "invoice-a.pdf",
  file_size: 2048,
  content_sha256: "a".repeat(64),
  status: "EXTRACTED",
  uploaded_by: "analyst@example.com",
  uploaded_at: "2026-08-30T09:00:00Z",
  updated_at: "2026-08-30T09:00:00Z",
};

const review = {
  ...genericResult,
  document,
  fields: genericRecords.fields,
  field_policies: {
    total: { confidence_threshold: 0.8, citation_required: false },
  },
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/results/${runId}`]}>
      <Routes>
        <Route path="/results/:runId" element={<ResultDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ResultDetailPage", () => {
  it("loads one review model and renders the header and drawer", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string) => {
      if (input === `/api/extractions/${runId}/review`) return { ok: true, json: async () => review };
      if (input === `/api/documents/${documentId}/pages?parse_run_id=parse-1`) return { ok: true, json: async () => [] };
      return { ok: false, json: async () => ({}) };
    }));

    renderPage();

    expect(await screen.findByText("invoice-a.pdf")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "invoice · v4" })).toBeInTheDocument();
    expect(screen.getByText("114")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      `/api/documents/${documentId}/pages?parse_run_id=parse-1`,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );

    fireEvent.click(screen.getByText("Run details"));
    expect(screen.getByText(runId)).toBeInTheDocument();
    expect(screen.getByText("abc123")).toBeInTheDocument();
  });

  it("re-running extraction starts a new run and navigates to it", async () => {
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === `/api/extractions/${runId}/review`) return { ok: true, json: async () => review };
      if (input === `/api/documents/${documentId}/pages?parse_run_id=parse-1`) return { ok: true, json: async () => [] };
      if (input === `/api/documents/${documentId}/extract` && init) {
        return { ok: true, json: async () => ({ extraction_run_id: "run-new" }) };
      }
      return { ok: false, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await screen.findByText("invoice-a.pdf");

    fireEvent.click(screen.getByRole("button", { name: /Re-run extraction/ }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        `/api/documents/${documentId}/extract`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ schema_id: "invoice", schema_version: 4 }),
        }),
      ),
    );
  });
});
