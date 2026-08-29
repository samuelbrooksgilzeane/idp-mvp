import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DocumentRecord } from "../types";
import type { CitationTarget } from "./DocumentViewer";
import { ExtractionPanel } from "./ExtractionPanel";

const documentId = "d0ed9896-da45-560a-8ddb-5b88d20dea1e";
const latestRunId = "13e7ac76-093f-481d-8360-42375bc8bda8";
const olderRunId = "19978032-845f-4831-a968-8dffcb54cef0";

const document: DocumentRecord = {
  document_id: documentId,
  case_id: null,
  template_id: "invoice_v1",
  use_case: "invoice",
  file_name: "invoice.pdf",
  file_size: 2048,
  content_sha256: "a".repeat(64),
  status: "EXTRACTED",
  uploaded_by: "analyst@example.com",
  uploaded_at: "2026-08-28T09:00:00Z",
  updated_at: "2026-08-29T12:00:00Z",
};

const schema = {
  schema_id: "invoice",
  schema_version: 1,
  display_name: "Invoice v1",
  use_case: "invoice",
  schema_hash: "b02b3c20d69e7f77ed76e45337107d4995aafab5ee411ab4f2e73b166876c640",
  status: "PRODUCTION",
};

function run(id: string) {
  return {
    extraction_run_id: id,
    document_id: documentId,
    parse_run_id: "2e22cfab-7cf5-4006-a302-66820fa9c8eb",
    schema_id: "invoice",
    schema_version: 1,
    schema_hash: schema.schema_hash,
    extractor_version: "2.1",
    status: "EXTRACTED",
    error_message: null,
    requested_by: "analyst@example.com",
    job_run_id: 1,
    started_at: "2026-08-29T11:54:20Z",
    completed_at: "2026-08-29T11:55:48Z",
  };
}

const runs = [run(latestRunId), run(olderRunId)];

const latestResult = {
  run: run(latestRunId),
  fields: [
    {
      field_path: "invoice_date",
      field_type: "string",
      value: "28-Jul-2011",
      value_string: "28-Jul-2011",
      confidence_score: 1.0,
      citation_ids: [0],
      citations: [{ id: 0, bbox: [{ coord: [3, 112, 417, 304], page_id: 0 }] }],
      extraction_error: null,
    },
    {
      field_path: "total",
      field_type: "number",
      value: 888.55,
      value_string: "888.55",
      confidence_score: 1.0,
      citation_ids: [3, 4],
      citations: [
        { id: 3, bbox: [{ coord: [893, 1542, 1222, 1579], page_id: 0 }] },
        { id: 4, bbox: [{ coord: [10, 20, 30, 40], page_id: 0 }] },
      ],
      extraction_error: null,
    },
    {
      field_path: "line_items[0].description", field_type: "string", value: "Widget A",
      value_string: "Widget A", confidence_score: 0.97, citation_ids: [], citations: [],
      extraction_error: null,
    },
    {
      field_path: "line_items[0].amount", field_type: "number", value: 274.95,
      value_string: "274.95", confidence_score: 0.99, citation_ids: [5],
      citations: [{ id: 5, bbox: [{ coord: [10, 20, 30, 40], page_id: 0 }] }],
      extraction_error: null,
    },
    {
      field_path: "line_items[1].description", field_type: "string", value: "Widget B",
      value_string: "Widget B", confidence_score: 0.96, citation_ids: [], citations: [],
      extraction_error: null,
    },
    {
      field_path: "subtotal",
      field_type: "number",
      value: null,
      value_string: null,
      confidence_score: null,
      citation_ids: [],
      citations: [],
      extraction_error: null,
    },
  ],
  candidate: {
    invoice_number: "INV/06-92/543",
    invoice_date: "2011-07-28",
    seller_name: "Mclean-Cochran",
    subtotal: null,
    discount_amount: "29.87",
    tax_amount: null,
    total_amount: "888.55",
    currency: "EUR",
  },
};

const olderResult = {
  ...latestResult,
  run: run(olderRunId),
  candidate: { ...latestResult.candidate, invoice_date: null },
  fields: latestResult.fields.map((field) =>
    field.field_path === "total" ? { ...field, value_string: "800.00", value: 800 } : field,
  ),
};

function panelFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = input.toString();
    if (url.includes("/extraction-runs")) return { ok: true, json: async () => runs };
    if (url.includes("/api/schemas")) return { ok: true, json: async () => [schema] };
    if (url.includes(`/extractions/${olderRunId}`)) {
      return { ok: true, json: async () => olderResult };
    }
    if (url.includes(`/extractions/${latestRunId}`)) {
      return { ok: true, json: async () => latestResult };
    }
    return { ok: true, json: async () => ({}) };
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ExtractionPanel", () => {
  it("renders provenance, typed values, confidence, and citation states for the latest run", async () => {
    vi.stubGlobal("fetch", panelFetch());
    render(
      <ExtractionPanel document={document} onViewEvidence={vi.fn()} onDocumentsChanged={vi.fn()} />,
    );

    // Provenance for the default (latest successful) run.
    await screen.findByText("invoice v1");
    expect(screen.getByText(/SHA b02b3c20d69e/)).toBeInTheDocument();
    expect(screen.getByText("ai_extract 2.1")).toBeInTheDocument();

    // Raw value preserved, typed value projected, confidence framed as metadata.
    expect(await screen.findByText("28-Jul-2011")).toBeInTheDocument();
    expect(screen.getByText("2011-07-28")).toBeInTheDocument();
    expect(screen.getAllByText("Model confidence 100%").length).toBeGreaterThan(0);

    // Null field renders an explicit state, not a blank cell.
    expect(screen.getAllByText("Not returned").length).toBeGreaterThan(0);
    expect(screen.getByText("No citation returned")).toBeInTheDocument();
  });

  it("raises a citation target with every bounding box when evidence is viewed", async () => {
    const onViewEvidence = vi.fn<(target: CitationTarget) => void>();
    vi.stubGlobal("fetch", panelFetch());
    render(
      <ExtractionPanel
        document={document}
        onViewEvidence={onViewEvidence}
        onDocumentsChanged={vi.fn()}
      />,
    );

    // Header-field controls are named exactly "View evidence"; line-item cells carry their
    // own path in the accessible name, so this selects the header controls only.
    const evidenceButtons = await screen.findAllByRole("button", { name: "View evidence" });
    // invoice_date + total are cited; subtotal is not.
    expect(evidenceButtons).toHaveLength(2);
    fireEvent.click(evidenceButtons[1]); // total, which has two citation boxes

    expect(onViewEvidence).toHaveBeenCalledTimes(1);
    const target = onViewEvidence.mock.calls[0][0];
    expect(target.fieldLabel).toBe("total");
    expect(target.pageId).toBe(0);
    expect(target.boxes).toHaveLength(2);
  });

  it("lets a prior run be inspected", async () => {
    vi.stubGlobal("fetch", panelFetch());
    render(
      <ExtractionPanel document={document} onViewEvidence={vi.fn()} onDocumentsChanged={vi.fn()} />,
    );

    // Latest run first: 888.55 shows as both the raw and the typed value.
    expect(await screen.findAllByText("888.55")).toHaveLength(2);

    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: olderRunId },
    });

    // The older run's distinct value is now shown.
    await waitFor(() => expect(screen.getByText("800.00")).toBeInTheDocument());
  });

  it("requires a successful parse before extraction", async () => {
    vi.stubGlobal("fetch", panelFetch());
    render(
      <ExtractionPanel
        document={{ ...document, status: "UPLOADED" }}
        onViewEvidence={vi.fn()}
        onDocumentsChanged={vi.fn()}
      />,
    );

    expect(
      await screen.findByText("A successful parse is required before extraction."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /extraction/i })).toBeDisabled();
  });
});

describe("ExtractionPanel line items", () => {
  it("groups repeated fields into a line table and links a cell to its evidence", async () => {
    const onViewEvidence = vi.fn<(target: CitationTarget) => void>();
    vi.stubGlobal("fetch", panelFetch());
    render(
      <ExtractionPanel
        document={document}
        onViewEvidence={onViewEvidence}
        onDocumentsChanged={vi.fn()}
      />,
    );

    // Repeated leaves are grouped, not listed as flat rows.
    expect(await screen.findByText("line items")).toBeInTheDocument();
    expect(screen.getByText("2 lines")).toBeInTheDocument();
    expect(screen.getByText("Widget A")).toBeInTheDocument();
    expect(screen.getByText("Widget B")).toBeInTheDocument();
    expect(screen.queryByText("line_items[0].amount")).not.toBeInTheDocument();

    // A ragged line shows an explicit state rather than a blank cell.
    expect(screen.getAllByText("Not returned").length).toBeGreaterThan(0);

    fireEvent.click(
      screen.getByRole("button", { name: /View evidence for line_items\[0\]\.amount/ }),
    );
    expect(onViewEvidence).toHaveBeenCalledTimes(1);
    expect(onViewEvidence.mock.calls[0][0].fieldLabel).toBe("line_items[0].amount");
  });
});
