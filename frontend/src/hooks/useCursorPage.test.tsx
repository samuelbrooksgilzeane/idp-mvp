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
