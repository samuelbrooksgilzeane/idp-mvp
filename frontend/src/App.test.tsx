import { invalidateListPages } from "./hooks/useCursorPage";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
  status: "UPLOADED" as const,
  uploaded_by: "analyst@example.com",
  uploaded_at: "2026-08-28T09:00:00Z",
  updated_at: "2026-08-28T09:00:00Z",
};

function renderApp(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup(); invalidateListPages();
  sessionStorage.clear();
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("renders document intake with proxied health and an empty registry", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => ({
      ok: true,
      json: async () => (input.toString().endsWith("/health") ? health : input.toString().includes("/documents/page?") ? { items: [], next_cursor: null } : []),
    }));
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(screen.getByRole("heading", { name: "Documents" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Upload PDFs" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Sections" })).toHaveTextContent("Documents");
    await waitFor(() => expect(screen.getByText("Reachable")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("No documents registered")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/health",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("links to the chat app only when one is configured", async () => {
    const chatUrl = "https://idp-chat.example.databricksapps.com";
    const respond = (url: string | null) => vi.fn(async (input: RequestInfo | URL) => ({
      ok: true,
      json: async () => {
        const path = input.toString();
        if (path.endsWith("/health")) return health;
        if (path.endsWith("/app-config")) return { project_name: "IDP MVP", chat_app_url: url };
        return path.includes("/documents/page?") ? { items: [], next_cursor: null } : [];
      },
    }));
    vi.stubGlobal("fetch", respond(null));
    renderApp();
    await waitFor(() => expect(screen.getByText("Reachable")).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: /Ask documents/ })).not.toBeInTheDocument();
    cleanup();

    vi.stubGlobal("fetch", respond(chatUrl));
    renderApp();
    const link = await screen.findByRole("link", { name: /Ask documents/ });
    expect(link).toHaveAttribute("href", chatUrl);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("links to the built-in chat page when chat is enabled", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => ({
      ok: true,
      json: async () => {
        const path = input.toString();
        if (path.endsWith("/health")) return health;
        if (path.endsWith("/app-config")) return { project_name: "IDP MVP", chat_app_url: null, chat_enabled: true };
        return path.includes("/documents/page?") ? { items: [], next_cursor: null } : [];
      },
    })));
    renderApp();
    const link = await screen.findByRole("link", { name: /Ask documents/ });
    expect(link).toHaveAttribute("href", "/chat");
    expect(link).not.toHaveAttribute("target");
  });

  it("shows registered documents without exposing their storage path", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => ({
        ok: true,
        json: async () => (input.toString().endsWith("/health") ? health : input.toString().includes("/documents/page?") ? { items: [document], next_cursor: null } : []),
      })),
    );

    renderApp();

    await waitFor(() => expect(screen.getByText("invoice-1042.pdf")).toBeInTheDocument());
    expect(screen.getByText("CASE-1042")).toBeInTheDocument();
    // The status also appears as a filter option, so assert the row's own status label.
    expect(screen.getByText("Uploaded", { selector: ".status-label" })).toBeInTheDocument();
    expect(screen.queryByText(/Volumes/)).not.toBeInTheDocument();
  });

  it("navigates from the registry to a document's own route", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input.toString();
        if (url.endsWith("/health")) return { ok: true, json: async () => health };
        if (url.endsWith(`/api/documents/${document.document_id}`)) {
          return { ok: true, json: async () => document };
        }
        if (url.endsWith("/parse-runs")) return { ok: true, json: async () => [] };
        if (url.endsWith("/pages")) return { ok: true, status: 200, json: async () => [] };
        return { ok: true, json: async () => input.toString().includes("/documents/page?") ? { items: [document], next_cursor: null } : [] };
      }),
    );

    renderApp();
    await waitFor(() => expect(screen.getByText("invoice-1042.pdf")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "invoice-1042.pdf" }));

    // The detail route renders its own heading and tabs.
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "invoice-1042.pdf" })).toBeInTheDocument(),
    );
    expect(await screen.findByRole("tab", { name: "Extraction" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /All documents/ })).toBeInTheDocument();
  });

  it("serves the schema contract from its own route", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => ({
      ok: true,
      json: async () => (input.toString().endsWith("/health") ? health : input.toString().includes("/documents/page?") ? { items: [], next_cursor: null } : []),
    }));
    vi.stubGlobal(
      "fetch",
      fetchMock,
    );

    renderApp("/schema");

    expect(
      await screen.findByRole("heading", { name: "Schemas" }),
    ).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Schema library" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([input]) => input.toString())).not.toContain("/api/documents");
    expect(fetchMock.mock.calls.map(([input]) => input.toString())).not.toContain("/api/documents/cases");
  });

  it("mounts a page once, under the resolved cache scope", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = input.toString();
      return {
        ok: true,
        json: async () => (
          url.endsWith("/health") ? health
            : url.endsWith("/limits") ? { cache_scope: "user-scope" }
              : url.startsWith("/api/extractions") ? { items: [], next_cursor: null } : []
        ),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp("/results");

    expect(await screen.findByText("No extraction runs")).toBeInTheDocument();
    // Resolving the scope after the page had mounted used to remount it and request it again.
    const listings = fetchMock.mock.calls.filter(([input]) =>
      input.toString().startsWith("/api/extractions?"),
    );
    expect(listings).toHaveLength(1);
  });

  it("does not load the registry when Results is opened directly", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = input.toString();
      return {
        ok: true,
        json: async () => (
          url.endsWith("/health") ? health : url.startsWith("/api/extractions") ? { items: [], next_cursor: null } : []
        ),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp("/results");

    expect(await screen.findByText("No extraction runs")).toBeInTheDocument();
    const requested = fetchMock.mock.calls.map(([input]) => input.toString());
    expect(requested.some((url) => url.startsWith("/api/extractions?"))).toBe(true);
    expect(requested.some((url) => url.startsWith("/api/documents/page"))).toBe(false);
    expect(requested).toContain("/api/documents/cases"); // Small server-side facet registry.
  });
});
