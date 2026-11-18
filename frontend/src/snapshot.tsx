import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, Snapshot } from "./api";

/** The snapshot every technology table, device page and topology view is looking at. "latest" follows
 *  the newest finished snapshot; a pinned id stays put until changed. Kept per browser. */
interface SnapState {
  ref: string;
  setRef: (r: string) => void;
  current: Snapshot | null;
  list: Snapshot[];
  running: number;
  reload: () => void;
}

const Ctx = createContext<SnapState>(null as any);
const KEY = "assurance.snapshot";
export const DIFFABLE = ["complete", "partial"];

export function SnapshotProvider({ children }: { children: React.ReactNode }) {
  const [ref, setRefState] = useState<string>(() => {
    try { return localStorage.getItem(KEY) || "latest"; } catch { return "latest"; }
  });
  const [all, setAll] = useState<Snapshot[]>([]);
  const reload = useCallback(() => {
    api<{ items: Snapshot[] }>("/snapshots", { params: { limit: 100 } }).then((r) => setAll(r.items)).catch(() => {});
  }, []);
  useEffect(() => {
    reload();
    const t = window.setInterval(reload, 30000);
    return () => window.clearInterval(t);
  }, [reload]);
  const setRef = useCallback((r: string) => {
    setRefState(r);
    try { localStorage.setItem(KEY, r); } catch { /* storage unavailable */ }
  }, []);
  const list = useMemo(() => all.filter((s) => DIFFABLE.includes(s.status)), [all]);
  const running = all.filter((s) => ["queued", "running", "finalizing"].includes(s.status)).length;
  const current = ref === "latest" ? list[0] || null : list.find((s) => s.id === ref) || null;
  useEffect(() => {  // a pinned snapshot that was deleted falls back to latest
    if (ref !== "latest" && all.length && !all.some((s) => s.id === ref)) setRef("latest");
  }, [ref, all, setRef]);
  const value = useMemo(() => ({ ref: current && ref !== "latest" ? current.id : "latest", setRef, current, list, running, reload }),
    [ref, setRef, current, list, running, reload]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useSnapshot = () => useContext(Ctx);
/** The id to put in API paths: the selected snapshot's id, or "latest" before the list has loaded. */
export function useSnapId(): string {
  const { current, ref } = useSnapshot();
  return current?.id || ref;
}
