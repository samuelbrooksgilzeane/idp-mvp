import { useEffect, useState } from "react";
export function useListSelection(scope: string) {
  const key = `idp-selection:${scope}`;
  const [selected, setSelected] = useState<Set<string>>(() => {
    try {
      const value = JSON.parse(sessionStorage.getItem(key) || "[]");
      return new Set(Array.isArray(value) ? value.filter(v => typeof v === "string").slice(0, 1000) : []);
    } catch { return new Set(); }
  });
  useEffect(() => {
    try { sessionStorage.setItem(key, JSON.stringify([...selected].slice(0, 1000))); } catch { /* Optional persistence. */ }
  }, [key, selected]);
  return [selected, setSelected] as const;
}
