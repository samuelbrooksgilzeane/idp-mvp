import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { invalidateListPages, useCursorPage } from "./useCursorPage";
afterEach(() => { cleanup(); invalidateListPages(); vi.unstubAllGlobals(); });
it("ignores a stale page after its filter changes", async () => {
  let resolveA!: (value: unknown) => void;
  vi.stubGlobal("fetch", vi.fn((url: string) => url === "/a" ? new Promise(resolve => { resolveA = resolve; })
    : Promise.resolve({ ok: true, json: async () => ({ items: ["B"], next_cursor: null }) })));
  const hook = renderHook(({ url }) => useCursorPage<string>(url), { initialProps: { url: "/a" } });
  hook.rerender({ url: "/b" });
  await waitFor(() => expect(hook.result.current.items).toEqual(["B"]));
  await act(async () => resolveA({ ok: true, json: async () => ({ items: ["A"], next_cursor: "stale" }) }));
  expect(hook.result.current.items).toEqual(["B"]);
  expect(hook.result.current.next_cursor).toBeNull();
});

it("keeps the current rows visible while the same page refreshes in the background", async () => {
  let resolveRefresh!: (value: unknown) => void;
  let calls = 0;
  vi.stubGlobal("fetch", vi.fn(() => {
    calls += 1;
    if (calls === 1) return Promise.resolve({ ok: true, json: async () => ({ items: ["A"], next_cursor: null }) });
    return new Promise(resolve => { resolveRefresh = resolve; });
  }));
  const hook = renderHook(({ revision }) => useCursorPage<string>("/docs", true, revision), { initialProps: { revision: 0 } });
  await waitFor(() => expect(hook.result.current.items).toEqual(["A"]));
  expect(hook.result.current.loading).toBe(false);
  hook.rerender({ revision: 1 });
  await waitFor(() => expect(hook.result.current.refreshing).toBe(true));
  expect(hook.result.current.loading).toBe(false);
  expect(hook.result.current.items).toEqual(["A"]);
  await act(async () => resolveRefresh({ ok: true, json: async () => ({ items: ["A", "B"], next_cursor: null }) }));
  await waitFor(() => expect(hook.result.current.items).toEqual(["A", "B"]));
  expect(hook.result.current.refreshing).toBe(false);
});
