export type ListContext = { url: string; endpoint: string; query: string; ids: string[];
  next: string | null; previous: string | undefined; kind: "documents" | "results" };
const previous = new Map<string, string>();
export function rememberCursor(key: string, next: string | null, cursor: string) {
  if (next) previous.set(`${key}:${next}`, cursor);
  if (previous.size > 100) previous.delete(previous.keys().next().value!);
}
export function previousCursor(key: string, cursor: string) { return previous.get(`${key}:${cursor}`); }
export function saveScroll(url: string) {
  try { sessionStorage.setItem(`idp-scroll:${url}`, String(window.scrollY)); } catch { /* Optional. */ }
}
export function restoreScroll(url: string) {
  const y = Number(sessionStorage.getItem(`idp-scroll:${url}`) || 0);
  if (y) window.scrollTo(0, y);
}
