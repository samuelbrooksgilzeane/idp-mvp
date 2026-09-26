import { useEffect, useState } from "react";
import type { ParsedElement, PageMetadata } from "../components/DocumentViewer";
import { canPrefetch, RequestCache } from "../lib/requestCache";

/** Per-viewer three-page cache: no full-document image or element preloading. */
export function useViewerPage(documentId: string, parseId: string | undefined, page: PageMetadata | null,
  next: PageMetadata | null, ready: boolean) {
  const [cache] = useState(() => new RequestCache<ParsedElement[]>(3, 2_000_000, 60_000));
  const [elements, setElements] = useState<ParsedElement[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { cache.clear(); }, [cache, documentId, parseId]);
  useEffect(() => {
    let active = true;
    setElements([]); setError(null);
    if (!page) return;
    setLoading(true);
    const parameters = new URLSearchParams({ page_id: String(page.page_id) });
    if (parseId) parameters.set("parse_run_id", parseId);
    const url = `/api/documents/${documentId}/elements?${parameters}`;
    void cache.get(url, async () => {
      const response = await fetch(url);
      if (!response.ok) throw new Error("The page elements could not be loaded.");
      return response.json() as Promise<ParsedElement[]>;
    }).then(value => { if (active) setElements(value); })
      .catch(cause => { if (active) setError(String(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [cache, documentId, parseId, page]);
  useEffect(() => {
    if (!ready || !next || !parseId || !canPrefetch()) return;
    const parameters = new URLSearchParams({ page_id: String(next.page_id), parse_run_id: parseId });
    const url = `/api/documents/${documentId}/elements?${parameters}`;
    void cache.get(url, async () => {
      const response = await fetch(url);
      if (!response.ok) throw new Error("Prefetch failed");
      return response.json() as Promise<ParsedElement[]>;
    }).catch(() => undefined);
    const image = new Image(); image.src = next.image_url;
    return () => { image.src = ""; };
  }, [cache, documentId, parseId, next, ready]);
  return { elements, loading, error };
}
