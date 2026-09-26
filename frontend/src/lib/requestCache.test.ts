import { expect, it, vi } from "vitest";
import { RequestCache } from "./requestCache";
it("evicts least-recently-used values and failed requests", async () => {
  const cache = new RequestCache<string>(2, 100, 10000);
  const load = vi.fn(async () => "value");
  await cache.get("a", load); await cache.get("b", load); await cache.get("a", load);
  await cache.get("c", load); await cache.get("b", load);
  expect(load).toHaveBeenCalledTimes(4);
  await expect(cache.get("bad", async () => { throw new Error("offline"); })).rejects.toThrow();
  expect(await cache.get("bad", load)).toBe("value");
});
it("enforces a byte budget and expiration", async () => {
  const cache = new RequestCache<string>(20, 10, 1);
  const large = vi.fn(async () => "x".repeat(20));
  await cache.get("large", large); await cache.get("large", large);
  expect(large).toHaveBeenCalledTimes(2);
  const load = vi.fn(async () => "a");
  await cache.get("small", load);
  await new Promise(resolve => setTimeout(resolve, 5));
  await cache.get("small", load);
  expect(load).toHaveBeenCalledTimes(2);
});
