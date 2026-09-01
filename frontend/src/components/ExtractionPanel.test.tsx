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
  candidates: [{
    invoice_number: "INV/06-92/543",
    invoice_date: "2011-07-28",
    seller_name: "Mclean-Cochran",
    subtotal: null,
    discount_amount: "29.87",
    tax_amount: null,
    total_amount: "888.55",
    currency: "EUR",
  }],
};

const olderResult = {
  ...latestResult,
  run: run(olderRunId),
  candidates: [{ ...latestResult.candidates[0], invoice_date: null }],
  fields: latestResult.fields.map((field) =>
    field.field_path === "total" ? { ...field, value_string: "800.00", value: 800 } : field,
  ),
};

function asReview(
  source: {
    run: ReturnType<typeof run>;
    fields: Array<{
      field_path: string;
      field_type: string;
      value: unknown;
      value_string: string | null;
      confidence_score: number | null;
      citation_ids: number[];
      citations: Array<{ id: number; bbox: Array<{ coord: number[]; page_id: number }> }>;
      extraction_error: string | null;
    }>;
  },
  hierarchy: Record<string, unknown>,
) {
  return {
    run: source.run,
    document,
    schema_id: source.run.schema_id,
    schema_version: source.run.schema_version,
    root_mode: "SINGLE_RECORD",
    result: hierarchy,
    fields: source.fields.map((field) => ({
      record_id: "root",
      schema_path: field.field_path.replace(/\[\d+\]/g, "[]"),
      instance_path: field.field_path,
      field_name: field.field_path.split(".").at(-1) ?? field.field_path,
      declared_type: field.field_type,
      value: field.value,
      value_string: field.value_string,
      confidence_score: field.confidence_score,
      citation_ids: field.citation_ids,
      citations: field.citations,
      validation_status: null,
      validation_message: field.extraction_error,
    })),
    field_policies: {},
  };
}

const latestReview = asReview(latestResult, {
  invoice_date: { value: "28-Jul-2011" },
  total: { value: 888.55 },
  line_items: [
    { description: { value: "Widget A" }, amount: { value: 274.95 } },
    { description: { value: "Widget B" } },
  ],
  subtotal: { value: null },
});
const olderReview = asReview(olderResult, {
  ...latestReview.result,
  total: { value: 800 },
});

function panelFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = input.toString();
    if (url.includes("/extraction-runs")) return { ok: true, json: async () => runs };
    if (url.includes("/api/schemas")) return { ok: true, json: async () => [schema] };
    if (url.includes(`/extractions/${olderRunId}`)) {
      return { ok: true, json: async () => olderReview };
    }
    if (url.includes(`/extractions/${latestRunId}`)) {
      return { ok: true, json: async () => latestReview };
    }
    return { ok: true, json: async () => ({}) };
  });
}

/** A v4 document stating two invoices, each with its own billed lines. */
function nestedField(path: string, value: string, cited = false) {
  return {
    field_path: path,
    field_type: "string",
    value,
    value_string: value,
    confidence_score: 0.98,
    citation_ids: cited ? [9] : [],
    citations: cited ? [{ id: 9, bbox: [{ coord: [1, 2, 3, 4], page_id: 1 }] }] : [],
    extraction_error: null,
  };
}

const nestedRunId = "6f1a2f70-6d4e-4f34-9a0f-2b6f9f5c4d21";
const nestedResult = {
  run: { ...run(nestedRunId), schema_version: 4 },
  candidates: [],
  fields: [
    nestedField("invoices[0].invoice_number", "INV-A-9001", true),
    nestedField("invoices[0].seller_name", "Northwind Trading Limited"),
    nestedField("invoices[0].line_items[0].description", "Widget assembly A"),
    nestedField("invoices[0].line_items[0].amount", "300.00"),
    nestedField("invoices[0].line_items[1].description", "Widget assembly B"),
    nestedField("invoices[0].line_items[1].amount", "150.00"),
    nestedField("invoices[1].invoice_number", "INV-B-4402"),
    nestedField("invoices[1].seller_name", "Sterling Components Limited"),
    nestedField("invoices[1].line_items[0].description", "Hydraulic coupling"),
    nestedField("invoices[1].line_items[0].amount", "100.00"),
  ],
};

const nestedReview = {
  ...asReview(nestedResult, {
    invoices: [
    {
      invoice_number: { value: "INV-A-9001" },
      seller_name: { value: "Northwind Trading Limited" },
      line_items: [
        { description: { value: "Widget assembly A" }, amount: { value: "300.00" } },
        { description: { value: "Widget assembly B" }, amount: { value: "150.00" } },
      ],
    },
    {
      invoice_number: { value: "INV-B-4402" },
      seller_name: { value: "Sterling Components Limited" },
      line_items: [
        { description: { value: "Hydraulic coupling" }, amount: { value: "100.00" } },
      ],
    },
    ],
  }),
  root_mode: "REPEATED_RECORDS",
};

function nestedFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = input.toString();
    if (url.includes("/extraction-runs")) {
      return { ok: true, json: async () => [{ ...run(nestedRunId), schema_version: 4 }] };
    }
    if (url.includes("/api/schemas")) return { ok: true, json: async () => [schema] };
    if (url.includes(`/extractions/${nestedRunId}`)) {
      return { ok: true, json: async () => nestedReview };
    }
    return { ok: true, json: async () => ({}) };
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ExtractionPanel", () => {
  it("renders provenance and values from the consolidated review response", async () => {
    const fetchMock = panelFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(
      <ExtractionPanel document={document} onViewEvidence={vi.fn()} onDocumentsChanged={vi.fn()} />,
    );

    // Provenance for the default (latest successful) run.
    await screen.findByText("invoice v1");
    expect(screen.getByText(/SHA b02b3c20d69e/)).toBeInTheDocument();
    expect(screen.getByText("ai_extract 2.1")).toBeInTheDocument();

    expect(await screen.findByText("28-Jul-2011")).toBeInTheDocument();
    expect(screen.getByText("888.55")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/extractions/${latestRunId}/review`,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
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

    fireEvent.click(await screen.findByRole("button", { name: "888.55" }));

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

    // Latest run first: the raw extracted value appears once, not duplicated by a typed column.
    expect(await screen.findAllByText("888.55")).toHaveLength(1);

    fireEvent.change(screen.getByRole("combobox", { name: /Extraction run/i }), {
      target: { value: olderRunId },
    });

    // The older run's distinct value is now shown.
    await waitFor(() => expect(screen.getByText("800")).toBeInTheDocument());
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

  it("lets the reviewer choose which published schema to apply", async () => {
    const beneficiarySchema = {
      ...schema,
      schema_id: "sf2823_14",
      display_name: "SF 2823-14 Designation of Beneficiary",
      schema_hash: "c".repeat(64),
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input.toString();
      if (url.includes("/extraction-runs")) return { ok: true, json: async () => [] };
      if (url.includes("/api/schemas")) {
        return { ok: true, json: async () => [schema, beneficiarySchema] };
      }
      if (url.endsWith("/extract") && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        expect(body).toMatchObject({ schema_id: "sf2823_14", schema_version: 1 });
        return { ok: true, json: async () => ({ ...run(latestRunId), status: "RUNNING" }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <ExtractionPanel
        document={{ ...document, status: "PARSED" }}
        onViewEvidence={vi.fn()}
        onDocumentsChanged={vi.fn()}
      />,
    );

    const selector = await screen.findByRole("combobox", { name: /Extraction schema/i });
    fireEvent.change(selector, { target: { value: "sf2823_14:1" } });
    fireEvent.click(screen.getByRole("button", { name: "Run extraction" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        `/api/documents/${documentId}/extract`,
        expect.objectContaining({ method: "POST" }),
      ),
    );
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
    expect(await screen.findByText("line_items (2)")).toBeInTheDocument();
    expect(screen.getByText("Widget A")).toBeInTheDocument();
    expect(screen.getByText("Widget B")).toBeInTheDocument();
    expect(screen.queryByText("line_items[0].amount")).not.toBeInTheDocument();

    // A ragged line shows an explicit state rather than a blank cell.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "274.95" }));
    expect(onViewEvidence).toHaveBeenCalledTimes(1);
    expect(onViewEvidence.mock.calls[0][0].fieldLabel).toBe("amount");
  });
  it("shows one invoice at a time, with its lines listed downwards", async () => {
    vi.stubGlobal("fetch", nestedFetch());
    render(
      <ExtractionPanel document={document} onViewEvidence={vi.fn()} onDocumentsChanged={vi.fn()} />,
    );

    // The first invoice is shown on its own, identified by its own values.
    expect(await screen.findByText("Record 1 of 2")).toBeInTheDocument();
    expect(screen.getByText("INV-A-9001")).toBeInTheDocument();
    expect(screen.getByText("Northwind Trading Limited")).toBeInTheDocument();
    // Its fields are labelled relative to the invoice, not by their full nested path.
    expect(screen.getByText("invoice_number")).toBeInTheDocument();
    expect(screen.queryByText("invoices[0].invoice_number")).not.toBeInTheDocument();

    // The lines are rows, so each line's leaves are the columns rather than one wide row.
    expect(screen.getByRole("columnheader", { name: "description" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "amount" })).toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "line items[0].amount" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Widget assembly A")).toBeInTheDocument();
    expect(screen.getByText("Widget assembly B")).toBeInTheDocument();
    // The second invoice's values are not on this page.
    expect(screen.queryByText("Hydraulic coupling")).not.toBeInTheDocument();
  });

  it("navigates between the invoices a document states", async () => {
    vi.stubGlobal("fetch", nestedFetch());
    render(
      <ExtractionPanel document={document} onViewEvidence={vi.fn()} onDocumentsChanged={vi.fn()} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Next record" }));
    expect(screen.getByText("Record 2 of 2")).toBeInTheDocument();
    expect(screen.getByText("Hydraulic coupling")).toBeInTheDocument();
    expect(screen.queryByText("Widget assembly A")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Previous record" }));
    expect(screen.getByText("Record 1 of 2")).toBeInTheDocument();
    expect(screen.getByText("Widget assembly A")).toBeInTheDocument();
  });

  it("raises evidence for the nested path behind the relative label", async () => {
    const onViewEvidence = vi.fn<(target: CitationTarget) => void>();
    vi.stubGlobal("fetch", nestedFetch());
    render(
      <ExtractionPanel
        document={document}
        onViewEvidence={onViewEvidence}
        onDocumentsChanged={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "INV-A-9001" }));
    // The label is stripped for reading; the citation must still carry the real path.
    expect(onViewEvidence).toHaveBeenCalledWith(
      expect.objectContaining({ fieldLabel: "invoice_number" }),
    );
  });
});
