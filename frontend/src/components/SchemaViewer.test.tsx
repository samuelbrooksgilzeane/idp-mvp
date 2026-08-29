import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SchemaViewer, type SchemaDetail, type SchemaSummary } from "./SchemaViewer";

const summaries: SchemaSummary[] = [
  {
    schema_id: "invoice",
    schema_version: 1,
    display_name: "Invoice v1",
    use_case: "invoice",
    schema_hash: "a".repeat(64),
    status: "PRODUCTION",
  },
];

const detail: SchemaDetail = {
  ...summaries[0],
  instructions: "Return only source-stated values and do not infer missing values.",
  fields: [
    {
      field_path: "invoice_number",
      label: "Invoice Number",
      field_type: "string",
      description: "Invoice identifier exactly as stated by the seller.",
      required: true,
      citation_required: true,
      confidence_threshold: 0.9,
      risk_tier: "high",
    },
    {
      field_path: "discount",
      label: "Discount",
      field_type: "number",
      description: "Discount stated on the source invoice.",
      required: false,
      citation_required: true,
      confidence_threshold: 0.8,
      risk_tier: "medium",
    },
  ],
  document_rules: [
    {
      rule_id: "invoice_total_reconciliation",
      rule_type: "arithmetic_reconciliation",
      description: "Check invoice arithmetic.",
      field_paths: ["subtotal", "discount", "tax", "total"],
      tolerance: 0.01,
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SchemaViewer", () => {
  it("renders an approved read-only contract and every field policy", async () => {
    const fetchMock = schemaFetch();
    vi.stubGlobal("fetch", fetchMock);

    render(<SchemaViewer useCase="invoice" />);

    expect(await screen.findByRole("heading", { name: "Approved field specification" })).toBeInTheDocument();
    expect(await screen.findByRole("combobox", { name: /Approved schema/ })).toHaveValue("invoice:1");
    expect(await screen.findByText("PRODUCTION")).toBeInTheDocument();
    expect(screen.getByText("SHA aaaaaaaaaaaa")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: /Invoice Number/ })).toHaveTextContent(
      "Invoice identifier exactly as stated by the seller.",
    );
    expect(screen.getByRole("cell", { name: "90%" })).toBeInTheDocument();
    expect(screen.getByText(/initial policy settings/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/schemas?status=PRODUCTION&use_case=invoice",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(fetchMock.mock.calls.every(([, init]) => init?.method === undefined)).toBe(true);
  });

  it("changes between deployed versions without sending schema JSON", async () => {
    const versionTwo = { ...summaries[0], schema_version: 2, display_name: "Invoice v2" };
    const fetchMock = schemaFetch([summaries[0], versionTwo]);
    vi.stubGlobal("fetch", fetchMock);
    render(<SchemaViewer useCase="invoice" />);

    const selector = await screen.findByRole("combobox", { name: /Approved schema/ });
    fireEvent.change(selector, { target: { value: "invoice:2" } });

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/schemas/invoice/versions/2",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
    expect(fetchMock.mock.calls.every(([, init]) => init?.body === undefined)).toBe(true);
  });

  it("shows explicit empty and registry error states", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => [] })));
    const { rerender } = render(<SchemaViewer useCase="contract" />);
    expect(await screen.findByText("No production schema")).toBeInTheDocument();

    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, json: async () => ({}) })));
    rerender(<SchemaViewer useCase="invoice" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Schema registry unavailable");
  });
});

function schemaFetch(available: SchemaSummary[] = summaries) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    void init;
    const url = input.toString();
    if (url.includes("?status=")) {
      return { ok: true, status: 200, json: async () => available };
    }
    const version = Number(url.split("/").at(-1));
    return {
      ok: true,
      status: 200,
      json: async () => ({ ...detail, schema_version: version }),
    };
  });
}
