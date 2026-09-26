/** Shared transports outlive individual consumers; values have bounded lifetime and size. */
export class RequestCache<T> {
  private entries = new Map<string, { promise: Promise<T>; bytes: number; expires: number }>();
  constructor(private count = 20, private byteLimit = 8_000_000, private ttl = 60_000) {}
  clear() { this.entries.clear(); }
  delete(key: string) { this.entries.delete(key); }
  get(key: string, load: () => Promise<T>): Promise<T> {
    const existing = this.entries.get(key);
    if (existing && existing.expires > Date.now()) {
      this.entries.delete(key); this.entries.set(key, existing); return existing.promise;
    }
    this.entries.delete(key);
    const entry: { promise: Promise<T>; bytes: number; expires: number } = { promise: Promise.resolve(null as T), bytes: 0, expires: Date.now() + this.ttl };
    entry.promise = load().then(value => {
      entry.bytes = JSON.stringify(value).length * 2;
      this.trim();
      return value;
    }).catch(error => {
      if (this.entries.get(key) === entry) this.entries.delete(key);
      throw error;
    });
    this.entries.set(key, entry); this.trim();
    return entry.promise;
  }
  private trim() {
    while (this.entries.size > this.count || [...this.entries.values()].reduce((sum, e) => sum + e.bytes, 0) > this.byteLimit) {
      this.entries.delete(this.entries.keys().next().value!);
    }
  }
}

let scope = "initial";
const resetters = new Set<() => void>();
export function cacheScope() { return scope; }
export function onScopeReset(reset: () => void) { resetters.add(reset); }
export function setCacheScope(value: string) {
  if (value === scope) return;
  scope = value; resetters.forEach(reset => reset());
}
export function canPrefetch() {
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection;
  return !document.hidden && !connection?.saveData && !["slow-2g", "2g"].includes(connection?.effectiveType || "");
}
