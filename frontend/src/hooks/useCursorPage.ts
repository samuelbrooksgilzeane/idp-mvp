import { useEffect, useState } from "react";
import { RequestCache, cacheScope, onScopeReset } from "../lib/requestCache";
type Page<T> = { items: T[]; next_cursor: string | null };
const pages = new RequestCache<Page<unknown>>(20, 4_000_000, 15_000);
onScopeReset(() => pages.clear());
export function invalidateListPages() { pages.clear(); }
export function useCursorPage<T>(url: string, enabled = true, revision = 0) {
  const scope = cacheScope();
  const [data, setData] = useState<Page<T>>({ items: [], next_cursor: null });
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    let complete = false;
    const controller = new AbortController();
    if (!enabled) { setLoading(false); return; }
    setLoading(true); setError(null);
    const key = `${scope}:${url}:${revision}`;
    void pages.get(key, async () => {
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok) throw new Error(response.status === 422
        ? "This page has expired. Reset the list to its first page."
        : "The list could not be loaded.");
      const data = await response.json() as Page<unknown>;
      if (controller.signal.aborted) throw new DOMException("Aborted", "AbortError");
      return data;
    }).then(value => { complete = true; if (active) { setData(value as Page<T>); setLoadedUrl(url); } })
      .catch(cause => { if (active) { setData({ items: [], next_cursor: null }); setLoadedUrl(url); setError(String(cause)); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); if (!complete) pages.delete(key); };
  }, [url, enabled, revision, scope]);
  return { ...(loadedUrl === url ? data : { items: [] as T[], next_cursor: null }),
    loading: enabled && (loading || loadedUrl !== url), error };
}
