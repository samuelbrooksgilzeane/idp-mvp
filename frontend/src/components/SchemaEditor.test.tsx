import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SchemaEditor } from "./SchemaEditor";

const draftSummary = {
  schema_id: "custom_form",
  schema_version: 1,
  display_name: "Custom Form",
  description: null,
  use_case: "generic",
  schema_hash: "d".repeat(64),
  status: "DRAFT",
  root_mode: "SINGLE_RECORD",
  is_editable: true,
  created_by: "analyst@example.com",
  created_at: "2026-08-28T09:00:00Z",
  published_at: null,
};

const draftDetail = {
  ...draftSummary,
  instructions: "Extract only stated values.",
  schema_tree: {
    field_1: { type: "string", description: "Describe what this field holds." },
  },
};

const publishedSummary = {
  schema_id: "invoice",
  schema_version: 4,
  display_name: "Invoice v4",
  description: "Seed template",
  use_case: "invoice",
  schema_hash: "e".repeat(64),
  status: "PUBLISHED",
  root_mode: "REPEATED_RECORDS",
  is_editable: false,
  created_by: "release@example.com",
  created_at: "2026-08-01T09:00:00Z",
  published_at: "2026-08-01T10:00:00Z",
};

const publishedDetail = {
  ...publishedSummary,
  instructions: "Return only source-stated values.",
  schema_tree: {
    invoices: {
      type: "array",
      description: "Every invoice.",
      items: {
        type: "object",
        description: "One invoice.",
        properties: {
          invoice_number: { type: "string", description: "Number." },
        },
      },
    },
  },
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function routeFetch(handlers: Record<string, (init?: RequestInit) => unknown>) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = input.toString();
    const key = Object.keys(handlers).find((pattern) => url.includes(pattern));
    if (!key) return { ok: true, json: async () => ({}) };
    return { ok: true, json: async () => handlers[key](init) };
  });
}

describe("SchemaEditor", () => {
  it("lists schemas grouped by id and shows each version's status", async () => {
    vi.stubGlobal(
      "fetch",
      routeFetch({
        "/api/schemas?status=ALL": () => [draftSummary, publishedSummary],
      }),
    );

    render(<SchemaEditor />);

    expect(await screen.findByText("Custom Form")).toBeInTheDocument();
    expect(screen.getByText("Invoice v4")).toBeInTheDocument();
    expect(screen.getByText("DRAFT")).toBeInTheDocument();
    expect(screen.getByText("PUBLISHED")).toBeInTheDocument();
  });

  it("creates a schema, edits its draft tree, validates it, and publishes it", async () => {
    let created = false;
    let saved = false;
    let published = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input.toString();
        const method = init?.method ?? "GET";
        if (url === "/api/schemas?status=ALL") {
          return { ok: true, json: async () => (created ? [draftSummary] : []) };
        }
        if (url === "/api/schemas" && method === "POST") {
          created = true;
          return { ok: true, json: async () => draftDetail };
        }
        if (url.endsWith("/versions/1") && url.includes("custom_form")) {
          return { ok: true, json: async () => draftDetail };
        }
        if (url.includes("/custom_form/validate") && method === "POST") {
          return { ok: true, json: async () => ({ valid: true, depth: 1, max_depth: 12, leaf_count: 1, max_leaves: 256, errors: [] }) };
        }
        if (url.includes("/custom_form/draft") && method === "PUT") {
          saved = true;
          return { ok: true, json: async () => draftDetail };
        }
        if (url.includes("/custom_form/publish") && method === "POST") {
          published = true;
          return { ok: true, json: async () => ({ ...draftDetail, status: "PUBLISHED", is_editable: false } ) };
        }
        return { ok: true, json: async () => ({}) };
      }),
    );

    render(<SchemaEditor />);

    fireEvent.click(await screen.findByRole("button", { name: /New schema/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Custom Form" } });
    fireEvent.click(screen.getByRole("button", { name: /Create draft/ }));

    expect(await screen.findByText("Fields")).toBeInTheDocument();
    expect(created).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Test schema" }));
    expect(await screen.findByText("Schema is valid.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(saved).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(published).toBe(true));
    expect(await screen.findByText(/now immutable and extractable/)).toBeInTheDocument();
  });

  it("shows a published schema read-only and clones it into a new editable draft", async () => {
    let cloned = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input.toString();
        const method = init?.method ?? "GET";
        if (url === "/api/schemas?status=ALL") return { ok: true, json: async () => [publishedSummary] };
        if (url.endsWith("/versions/4")) return { ok: true, json: async () => publishedDetail };
        if (url.includes("/clone") && method === "POST") {
          cloned = true;
          return {
            ok: true,
            json: async () => ({ ...publishedDetail, schema_version: 5, status: "DRAFT", is_editable: true }),
          };
        }
        return { ok: true, json: async () => ({}) };
      }),
    );

    render(<SchemaEditor />);

    const list = await screen.findByRole("navigation", { name: "Registered schemas" });
    fireEvent.click(within(list).getByText("v4"));

    expect(await screen.findByText("Return only source-stated values.")).toBeInTheDocument();
    // A published schema is read only: no save/publish controls are offered.
    expect(screen.queryByRole("button", { name: "Save draft" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Clone to a new draft/ }));
    await waitFor(() => expect(cloned).toBe(true));
    expect(await screen.findByText(/Draft version 5 created for editing/)).toBeInTheDocument();
  });
});
