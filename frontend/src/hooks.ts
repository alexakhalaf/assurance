import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

/** Fetch on mount and when deps change; optional polling while `poll` returns true. */
export function useApi<T>(path: string | null, params?: any, poll?: (d: T) => boolean) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const key = path + JSON.stringify(params || {});
  const timer = useRef<number>();

  const load = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      const d = await api<T>(path, { params });
      setData(d);
      setError(null);
      if (poll && poll(d)) timer.current = window.setTimeout(load, 2500);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    load();
    return () => window.clearTimeout(timer.current);
  }, [load]);

  return { data, error, loading, reload: load, setData };
}

export function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function ago(iso?: string | null): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function short(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.map(short).join(", ") || "—";
  if (typeof v === "object") return Object.values(v as object).filter((x) => x !== null).map(short).join(" via ");
  return String(v);
}
