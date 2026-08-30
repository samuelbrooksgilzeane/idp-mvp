import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BatchActions } from "./BatchActions";

const schema = {
  schema_id: "invoice",
  schema_version: 3,
  display_name: "Invoice",
  status: "PRODUCTION",
};
const ids = ["doc-a", "doc-b"];

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("BatchActions", () => {
  it("submits the selection as one batch and reports completion", async () => {
    let settled = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input.toString();
      if (url.includes("/api/schemas")) return { ok: true, json: async () => [schema] };
      if (url === "/api/batches/parse" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            kind: "parse",
            job_run_id: 77,
            requested: 2,
            accepted: 2,
            members: ids.map((id) => ({ document_id: id, run_id: `r-${id}`, status: "RUNNING" })),
            errors: [],
          }),
        };
      }
      if (url.startsWith("/api/batches/parse/77")) {
        const body = settled
          ? { kind: "parse", job_run_id: 77, total: 2, running: 0, succeeded: 2, failed: 0 }
          : { kind: "parse", job_run_id: 77, total: 2, running: 1, succeeded: 1, failed: 0 };
        settled = true;
        return { ok: true, json: async () => body };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <BatchActions selectedIds={ids} onClear={vi.fn()} onDocumentsChanged={vi.fn()} />,
    );

    expect(screen.getByText("2 selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Parse selected/ }));

    await waitFor(
      () => expect(screen.getByText("All 2 documents completed.")).toBeInTheDocument(),
      { timeout: 4000 },
    );
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    // One request carries the whole selection; the engine is never named by the client.
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ document_ids: ids });
  });

  it("reports skipped documents without claiming the batch failed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input.toString();
        if (url.includes("/api/schemas")) return { ok: true, json: async () => [schema] };
        if (url === "/api/batches/extract" && init?.method === "POST") {
          return {
            ok: true,
            json: async () => ({
              kind: "extract",
              job_run_id: null,
              requested: 2,
              accepted: 0,
              members: [],
              errors: [
                {
                  document_id: "doc-a",
                  code: "SUCCESSFUL_PARSE_REQUIRED",
                  message: "A successful parse is required before extraction.",
                },
              ],
            }),
          };
        }
        return { ok: true, json: async () => ({}) };
      }),
    );

    render(
      <BatchActions selectedIds={ids} onClear={vi.fn()} onDocumentsChanged={vi.fn()} />,
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Extract selected/ })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: /Extract selected/ }));

    expect(
      await screen.findByText(/1 of 2 skipped: A successful parse is required/),
    ).toBeInTheDocument();
  });

  it("submits the schema identity when extracting", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input.toString();
      if (url.includes("/api/schemas")) return { ok: true, json: async () => [schema] };
      if (url === "/api/batches/extract" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            kind: "extract", job_run_id: 5, requested: 2, accepted: 2, members: [], errors: [],
          }),
        };
      }
      return {
        ok: true,
        json: async () => ({
          kind: "extract", job_run_id: 5, total: 2, running: 0, succeeded: 2, failed: 0,
        }),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <BatchActions selectedIds={ids} onClear={vi.fn()} onDocumentsChanged={vi.fn()} />,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Extract selected/ })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: /Extract selected/ }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
      expect(JSON.parse(String(post?.[1]?.body))).toEqual({
        document_ids: ids,
        schema_id: "invoice",
        schema_version: 3,
      });
    });
  });

  it("stays hidden when nothing is selected", () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => [] })));
    const { container } = render(
      <BatchActions selectedIds={[]} onClear={vi.fn()} onDocumentsChanged={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
