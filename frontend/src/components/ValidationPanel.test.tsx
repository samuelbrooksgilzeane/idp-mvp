import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DocumentRecord } from "../types";
import type { CitationTarget } from "./DocumentViewer";
import { ValidationPanel } from "./ValidationPanel";

const documentId = "d0ed9896-da45-560a-8ddb-5b88d20dea1e";
const runId = "8f0f0e2a-1111-4bbb-9ccc-2d2d2d2d2d2d";

const document: DocumentRecord = {
  document_id: documentId, case_id: null, template_id: "invoice_v1", use_case: "invoice",
  file_name: "invoice.pdf", file_size: 2048, content_sha256: "a".repeat(64),
  status: "REVIEW_REQUIRED", uploaded_by: "analyst@example.com",
  uploaded_at: "2026-08-28T09:00:00Z", updated_at: "2026-08-29T12:00:00Z",
};

const run = {
  validation_run_id: runId, document_id: documentId,
  extraction_run_id: "13e7ac76-093f-481d-8360-42375bc8bda8", schema_id: "invoice",
  schema_version: 2, schema_hash: "b".repeat(64), validator_version: "1.0.0",
  status: "COMPLETED", document_status: "REVIEW_REQUIRED", requested_by: "analyst@example.com",
  started_at: "2026-08-29T12:00:00Z", completed_at: "2026-08-29T12:00:01Z",
};

const report = {
  run,
  summary: {
    total: 4, passed: 1, failed: 1, uncertain: 1, skipped: 1,
    blocking: 1, warning: 1, info: 0,
  },
  results: [
    {
      rule_id: "invoice_total_reconciliation", field_path: "total", validator_type: "BUSINESS",
      severity: "BLOCKING", status: "FAIL",
      message: "Reconciliation is out by 885.00, beyond the configured tolerance of 0.01.",
      actual_value: "999.00", expected_value: "114.00", evidence: null,
      validator_version: "1.0.0",
    },
    {
      rule_id: "grounding", field_path: "seller_name", validator_type: "TECHNICAL",
      severity: "WARNING", status: "UNCERTAIN",
      message: "seller_name could not be located in the parsed document text.",
      actual_value: "Ghost Ltd", expected_value: null, evidence: null,
      validator_version: "1.0.0",
    },
    {
      rule_id: "citation_presence", field_path: "subtotal", validator_type: "TECHNICAL",
      severity: "INFO", status: "SKIPPED",
      message: "subtotal returned no value, so citation evidence does not apply.",
      actual_value: null, expected_value: null, evidence: null, validator_version: "1.0.0",
    },
    {
      rule_id: "provenance", field_path: null, validator_type: "TECHNICAL",
      severity: "INFO", status: "PASS", message: "Extraction and parse provenance are intact.",
      actual_value: null, expected_value: null, evidence: null, validator_version: "1.0.0",
    },
  ],
};

const extraction = {
  fields: [
    {
      field_path: "total",
      citations: [{ id: 3, bbox: [{ coord: [893, 1542, 1222, 1579], page_id: 0 }] }],
    },
    { field_path: "seller_name", citations: [] },
  ],
};

function panelFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = input.toString();
    if (url.includes("/validation-runs")) return { ok: true, json: async () => [run] };
    if (url.includes(`/validations/${runId}`)) return { ok: true, json: async () => report };
    if (url.includes("/extractions/")) return { ok: true, json: async () => extraction };
    return { ok: true, json: async () => ({}) };
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ValidationPanel", () => {
  it("shows the outcome, provenance and non-colour status labels", async () => {
    vi.stubGlobal("fetch", panelFetch());
    render(
      <ValidationPanel document={document} onViewEvidence={vi.fn()} onDocumentsChanged={vi.fn()} />,
    );

    expect(await screen.findByText("Review required")).toBeInTheDocument();
    expect(screen.getByText("invoice v2")).toBeInTheDocument();
    expect(screen.getByText("validator 1.0.0")).toBeInTheDocument();
    // Status is stated in text, not by colour alone.
    expect(screen.getByText("FAIL")).toBeInTheDocument();
    expect(screen.getByText("UNCERTAIN")).toBeInTheDocument();
    expect(screen.getByText(/Neither outcome means a person has approved/i)).toBeInTheDocument();
  });

  it("defaults to issues and can reveal passing and skipped results", async () => {
    vi.stubGlobal("fetch", panelFetch());
    render(
      <ValidationPanel document={document} onViewEvidence={vi.fn()} onDocumentsChanged={vi.fn()} />,
    );

    // Issues only: the failure and the uncertainty, not the pass or the skip.
    expect(await screen.findByText("invoice_total_reconciliation")).toBeInTheDocument();
    expect(screen.getByText("grounding")).toBeInTheDocument();
    expect(screen.queryByText("provenance")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "All" }));
    await waitFor(() => expect(screen.getByText("provenance")).toBeInTheDocument());
    expect(screen.getByText("SKIPPED")).toBeInTheDocument();
  });

  it("links a failing field to its cited region", async () => {
    const onViewEvidence = vi.fn<(target: CitationTarget) => void>();
    vi.stubGlobal("fetch", panelFetch());
    render(
      <ValidationPanel
        document={document}
        onViewEvidence={onViewEvidence}
        onDocumentsChanged={vi.fn()}
      />,
    );

    const evidence = await screen.findByRole("button", { name: /View evidence/ });
    fireEvent.click(evidence);

    expect(onViewEvidence).toHaveBeenCalledTimes(1);
    const target = onViewEvidence.mock.calls[0][0];
    expect(target.fieldLabel).toBe("total");
    expect(target.pageId).toBe(0);
    expect(target.boxes[0].coord).toEqual([893, 1542, 1222, 1579]);
  });

  it("requires a successful extraction before validating", async () => {
    vi.stubGlobal("fetch", panelFetch());
    render(
      <ValidationPanel
        document={{ ...document, status: "PARSED" }}
        onViewEvidence={vi.fn()}
        onDocumentsChanged={vi.fn()}
      />,
    );

    expect(
      await screen.findByText("A successful extraction is required before validation."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /validation/i })).toBeDisabled();
  });
});
