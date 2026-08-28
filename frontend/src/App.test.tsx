import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("renders the foundation workflow and reports proxied API health", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          status: "ok",
          mode: "mock",
          application_name: "IDP MVP",
          configuration: {},
        }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(screen.getByRole("heading", { name: "MVP not configured" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "MVP workflow" })).toHaveTextContent("Foundation");
    await waitFor(() => expect(screen.getByText("Reachable")).toBeInTheDocument());
    expect(screen.getByText("mock")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/health", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it("shows a clear unavailable state when the health endpoint fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    render(<App />);

    await waitFor(() => expect(screen.getByText("Unavailable")).toBeInTheDocument());
  });
});
