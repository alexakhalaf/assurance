// Thin typed client for /api/v1. The session token lives in sessionStorage (tab-scoped).
const KEY = "assurance.token";

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
  }
}

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setToken(t: string | null) {
  try {
    if (t) sessionStorage.setItem(KEY, t);
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage unavailable */
  }
}

type Params = Record<string, string | number | boolean | string[] | undefined | null>;

export function qs(params?: Params): string {
  if (!params) return "";
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => u.append(k, x));
    else u.append(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

export async function api<T = any>(path: string, opts: RequestInit & { params?: Params } = {}): Promise<T> {
  const headers = new Headers(opts.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (opts.body && !(opts.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(`/api/v1${path}${qs(opts.params)}`, { ...opts, headers });
  if (res.status === 401 && !path.startsWith("/auth/login")) {
    setToken(null);
    window.dispatchEvent(new Event("assurance:logout"));
  }
  if (!res.ok) {
    let body: any = null;
    try {
      body = await res.json();
    } catch {
      /* not json */
    }
    const errs = body?.errors?.map((e: any) => `${e.loc.slice(1).join(".")}: ${e.msg}`).join("; ");
    throw new ApiError(res.status, errs || body?.detail || body?.title || res.statusText, body);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("json") ? res.json() : res.text()) as Promise<T>;
}

export function download(path: string, params?: Params) {
  // Authenticated download: fetch as blob, then save.
  const token = getToken();
  fetch(`/api/v1${path}${qs(params)}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
    .then(async (r) => {
      if (!r.ok) throw new Error(r.statusText);
      const cd = r.headers.get("content-disposition") || "";
      const name = /filename="([^"]+)"/.exec(cd)?.[1] || "export.csv";
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      URL.revokeObjectURL(url);
    })
    .catch((e) => alert(`Download failed: ${e.message}`));
}

// ---- types
export interface User { id: number; username: string; role: "viewer" | "operator" | "admin"; }
export interface Device {
  id: number; name: string; host: string; platform: string; port: number; site: string | null;
  role: string | null; tags: string[]; status: string; credential: string | null; hostname: string | null;
  model: string | null; serial: string | null; os_version: string | null; last_collected_at: string | null;
  last_collection_status: string | null; ssh_host_key_type: string | null; ssh_host_key_fingerprint: string | null;
  ssh_host_key_pinned_at: string | null;
}
export interface Snapshot {
  id: string; status: string; trigger: string; requested_by: string | null; scope: Record<string, unknown>;
  labels: string[]; created_at: string; started_at: string | null; finished_at: string | null;
  device_count: number; summary: any;
}
export interface Collection {
  device_name: string; device_meta: any; status: string; duration_ms: number | null; error: string | null;
  warnings: string[]; row_counts: Record<string, number>;
}
export interface Change {
  device: string; table: string; key: string; change: "added" | "removed" | "changed";
  before: Record<string, any> | null; after: Record<string, any> | null; fields_changed: string[];
}
export interface Diff {
  from: { id: string; created_at: string; labels: string[] }; to: { id: string; created_at: string; labels: string[] };
  summary: any; changes: Change[]; total: number;
}
export const TABLES = ["facts", "interfaces", "routes", "arp", "neighbors", "bgp", "ospf", "mac", "vlans", "vrfs",
  "hardware", "portchannels", "stp", "fhrp", "ntp", "config"] as const;
export interface CheckSummary { status: string; severity: string; violations: number; }
export interface Violation { rule?: string; device: string; key: string; message: string; severity?: string; data?: any; }
