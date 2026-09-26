import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BatchActions } from "./BatchActions";

beforeEach(() => { localStorage.clear(); vi.spyOn(document, "hidden", "get").mockReturnValue(false); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("submits durable extraction and offers failed-member retry after completion", async () => {
  const status = { batch_id: "batch", state: "COMPLETE", total: 1, validated: 1, counts: { FAILED: 1 }, terminal: true };
  const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify(
    url.startsWith("/api/schemas") ? [{ schema_id: "invoice", schema_version: 3, display_name: "Invoice", status: "PUBLISHED" }]
      : url.includes("/items") ? { items: [{ ordinal: 0, document_id: "doc", document_name: "invoice.pdf", state: "FAILED", error_message: "Source changed" }], next_cursor: null }
      : status,
  ), { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  render(<MemoryRouter><BatchActions bulkExtraction selectedIds={["doc"]} onClear={vi.fn()} onDocumentsChanged={vi.fn()} /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole("button", { name: "Extract selected" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Extract selected" }));
  await screen.findByRole("button", { name: "Retry failed documents" });
  expect(fetcher).toHaveBeenCalledWith("/api/extraction-batches", expect.objectContaining({ method: "POST" }));
  expect(await screen.findByRole("link", { name: "invoice.pdf" })).toHaveAttribute("href", "/documents/doc");
  fireEvent.click(screen.getByRole("button", { name: "Retry failed documents" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith("/api/extraction-batches/batch/retry", expect.objectContaining({ method: "POST" })));
});
