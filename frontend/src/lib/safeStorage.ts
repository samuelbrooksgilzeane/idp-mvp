// Browser storage may be blocked (privacy settings), full, or hold an old shape. These calls fail
// soft: storage only adds reload recovery and must never stop the action itself.
export function readJson<T>(key: string): T | null {
  try { return JSON.parse(localStorage.getItem(key) ?? "null") as T | null; } catch { return null; }
}

/** False when the value could not be saved, so a reload will not recover it. */
export function writeJson(key: string, value: unknown): boolean {
  try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
}

export function removeKey(key: string) {
  try { localStorage.removeItem(key); } catch { /* Blocked storage holds nothing to remove. */ }
}
