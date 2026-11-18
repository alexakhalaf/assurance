import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

/** Fetch on mount and when path/params change; optional polling while `poll` returns true.
 *  Responses that arrive after a newer request started, or after unmount, are dropped. */
export function useApi<T>(path: string | null, params?: any, poll?: (d: T) => boolean) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const key = path + JSON.stringify(params || {});
  const timer = useRef<number>();
  const seq = useRef(0);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const load = useCallback(async () => {
    window.clearTimeout(timer.current);
    if (!path) { setData(null); return; }
    const my = ++seq.current;
    setLoading(true);
    try {
      const d = await api<T>(path, { params });
      if (!alive.current || my !== seq.current) return;
      setData(d);
      setError(null);
      if (poll && poll(d)) timer.current = window.setTimeout(load, 2500);
    } catch (e: any) {
      if (!alive.current || my !== seq.current) return;
      setError(e.message);
    } finally {
      if (alive.current && my === seq.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    load();
    return () => window.clearTimeout(timer.current);
  }, [load]);

  return { data, error, loading, reload: load, setData };
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function useLocal<T>(key: string, init: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try {
      const s = localStorage.getItem(key);
      return s ? (JSON.parse(s) as T) : init;
    } catch {
      return init;
    }
  });
  const set = useCallback((nv: T) => {
    setV(nv);
    try { localStorage.setItem(key, JSON.stringify(nv)); } catch { /* storage unavailable */ }
  }, [key]);
  return [v, set];
}

export function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function fmtShort(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function ago(iso?: string | null): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return "in " + until(iso);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

function until(iso: string): string {
  const s = (new Date(iso).getTime() - Date.now()) / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} d`;
}

export function duration(ms?: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

export function speed(mbps?: number | null): string {
  if (mbps == null) return "—";
  if (mbps >= 1000) return `${mbps / 1000 >= 100 ? Math.round(mbps / 1000) : +(mbps / 1000).toFixed(1)}G`;
  return `${mbps}M`;
}

export function short(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (Array.isArray(v)) return v.map(short).join(", ") || "—";
  if (typeof v === "object") return Object.values(v as object).filter((x) => x !== null && x !== undefined).map(short).join(" via ");
  return String(v);
}

export function pct(n: number, d: number): string {
  return d ? `${Math.round((n / d) * 100)}%` : "—";
}
