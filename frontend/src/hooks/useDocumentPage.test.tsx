import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { useDocumentPage } from "./useDocumentPage";

function wrapper({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("useDocumentPage", () => {
  it("ignores an obsolete response even if the transport ignores abort", async () => {
    let finishFirst!: (response: unknown) => void;
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => new Promise((resolve) => { finishFirst = resolve; }))
      .mockResolvedValueOnce({ ok: true, json: async () => ({ items: [], next_cursor: "new-cursor" }) });
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useDocumentPage(true), { wrapper });
    act(() => result.current.changeFilter("search", "new"));
    await waitFor(() => expect(result.current.nextCursor).toBe("new-cursor"));
    await act(async () => {
      finishFirst({ ok: true, json: async () => ({ items: [], next_cursor: "obsolete" }) });
    });
    expect(result.current.nextCursor).toBe("new-cursor");
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
    expect(fetchMock.mock.calls[1][0]).toContain("search=new");
  });

  it("uses server cursors, remembers the previous page and resets on filter changes", async () => {
    const fetchMock = vi.fn(async (url: string) => ({
      ok: true,
      json: async () => ({ items: [], next_cursor: url.includes("cursor=") ? null : "page-two" }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useDocumentPage(true), { wrapper });
    await waitFor(() => expect(result.current.nextCursor).toBe("page-two"));
    act(() => result.current.changeCursor("page-two"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.previousCursor).toBe("");
    expect(fetchMock.mock.calls.at(-1)?.[0]).toContain("limit=50&cursor=page-two");
    act(() => result.current.changeFilter("status", "PARSED"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.cursor).toBe("");
    expect(fetchMock.mock.calls.at(-1)?.[0]).toContain("status=PARSED");
    expect(fetchMock.mock.calls.at(-1)?.[0]).not.toContain("cursor=");
  });

  it("exposes invalid cursor errors and does not fetch off the registry route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 422 });
    vi.stubGlobal("fetch", fetchMock);
    const { result, rerender } = renderHook(({ enabled }) => useDocumentPage(enabled), {
      wrapper, initialProps: { enabled: false },
    });
    expect(fetchMock).not.toHaveBeenCalled();
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.error).toContain("Reset the list"));
    expect(result.current.documents).toEqual([]);
  });
});
