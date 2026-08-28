import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const health = {
  status: "ok",
  mode: "mock",
  application_name: "IDP MVP",
  configuration: {},
};

const document = {
  document_id: "9e4ef80e-fef3-5e13-ae29-f8dc585b15cb",
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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("renders document intake with proxied health and an empty registry", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => ({
      ok: true,
      json: async () => input.toString().endsWith("/health") ? health : [],
    }));
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(screen.getByRole("heading", { name: "Document intake" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Upload PDFs" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "MVP workflow" })).toHaveTextContent("Ingest");
    await waitFor(() => expect(screen.getByText("Reachable")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("No documents registered")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/health",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("shows registered documents without exposing their storage path", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => ({
        ok: true,
        json: async () => input.toString().endsWith("/health") ? health : [document],
      })),
    );

    render(<App />);

    await waitFor(() => expect(screen.getByText("invoice-1042.pdf")).toBeInTheDocument());
    expect(screen.getByText("CASE-1042")).toBeInTheDocument();
    expect(screen.getByText("UPLOADED")).toBeInTheDocument();
    expect(screen.queryByText(/Volumes/)).not.toBeInTheDocument();
  });

  it("shows a deterministic duplicate explanation from the upload API", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (input.toString().endsWith("/health")) {
        return { ok: true, json: async () => health };
      }
      if (init?.method === "POST") {
        return {
          ok: false,
          json: async () => ({
            error: {
              code: "DOCUMENT_DUPLICATE",
              message: "This PDF is already registered as invoice-1042.pdf.",
            },
          }),
        };
      }
      return { ok: true, json: async () => [document] };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await waitFor(() => expect(screen.getByText("invoice-1042.pdf")).toBeInTheDocument());

    const file = new File(["%PDF-1.7"], "invoice-copy.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/Choose PDF files/), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Register documents" }));

    await waitFor(() =>
      expect(
        screen.getByText("This PDF is already registered as invoice-1042.pdf."),
      ).toBeInTheDocument(),
    );
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[1]?.body).toBeInstanceOf(FormData);
    expect(Array.from((post?.[1]?.body as FormData).keys())).not.toContain("storage_path");
  });
});
