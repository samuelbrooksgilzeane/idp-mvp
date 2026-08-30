import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GenericResultView, type FieldPolicy } from "./GenericResultView";
import type { GenericField } from "../types";

function leaf(value: unknown) {
  return { value, confidence_score: 0.95, citation_ids: [], citations: [] };
}

function field(overrides: Partial<GenericField> & Pick<GenericField, "instance_path" | "schema_path" | "field_name">): GenericField {
  return {
    record_id: "record-1",
    declared_type: "string",
    value: null,
    value_string: null,
    confidence_score: 0.95,
    citation_ids: [],
    citations: [],
    validation_status: null,
    validation_message: null,
    ...overrides,
  };
}

afterEach(() => cleanup());

describe("GenericResultView", () => {
  it("renders a flat SINGLE_RECORD schema's leaves", () => {
    render(
      <GenericResultView
        rootMode="SINGLE_RECORD"
        hierarchy={{ seller_name: leaf("Acme Supplies"), total: leaf(114) }}
        fields={[]}
        fieldPolicies={new Map()}
        onViewEvidence={vi.fn()}
      />,
    );
    expect(screen.getByText("seller_name")).toBeInTheDocument();
    expect(screen.getByText("Acme Supplies")).toBeInTheDocument();
    expect(screen.getByText("total")).toBeInTheDocument();
    expect(screen.getByText("114")).toBeInTheDocument();
  });

  it("flags a field below its confidence threshold and calls onViewEvidence with its citation", () => {
    const onViewEvidence = vi.fn();
    const fields: GenericField[] = [
      field({
        instance_path: "total",
        schema_path: "total",
        field_name: "total",
        confidence_score: 0.4,
        citations: [{ id: 1, bbox: [{ page_id: 2, coord: [1, 2, 3, 4] }] }],
      }),
    ];
    render(
      <GenericResultView
        rootMode="SINGLE_RECORD"
        hierarchy={{ total: { value: 114, confidence_score: 0.4, citation_ids: [1], citations: [] } }}
        fields={fields}
        fieldPolicies={new Map<string, FieldPolicy>([["total", { confidenceThreshold: 0.8, citationRequired: false }]])}
        onViewEvidence={onViewEvidence}
      />,
    );
    expect(screen.getByText("Low confidence")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /114/ }));
    expect(onViewEvidence).toHaveBeenCalledWith(
      expect.objectContaining({ pageId: 2, fieldLabel: "total", boxes: [{ page_id: 2, coord: [1, 2, 3, 4] }] }),
    );
  });

  it("paginates REPEATED_RECORDS root records", () => {
    render(
      <GenericResultView
        rootMode="REPEATED_RECORDS"
        hierarchy={{
          invoices: [
            { invoice_number: leaf("INV-1") },
            { invoice_number: leaf("INV-2") },
          ],
        }}
        fields={[]}
        fieldPolicies={new Map()}
        onViewEvidence={vi.fn()}
      />,
    );
    expect(screen.getByText("Record 1 of 2")).toBeInTheDocument();
    expect(screen.getByText("INV-1")).toBeInTheDocument();
    expect(screen.queryByText("INV-2")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next record" }));
    expect(screen.getByText("Record 2 of 2")).toBeInTheDocument();
    expect(screen.getByText("INV-2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next record" })).toBeDisabled();
  });

  it("renders a repeated scalar array as a flat nested table, and an empty array as text", () => {
    render(
      <GenericResultView
        rootMode="SINGLE_RECORD"
        hierarchy={{
          line_items: [
            { description: leaf("Widget"), amount: leaf(10) },
            { description: leaf("Gadget"), amount: leaf(20) },
          ],
          notes: [],
        }}
        fields={[]}
        fieldPolicies={new Map()}
        onViewEvidence={vi.fn()}
      />,
    );
    expect(screen.getByText("line_items (2)")).toBeInTheDocument();
    expect(screen.getByText("Widget")).toBeInTheDocument();
    expect(screen.getByText("Gadget")).toBeInTheDocument();
    expect(screen.getByText("notes: none stated")).toBeInTheDocument();
  });
});
