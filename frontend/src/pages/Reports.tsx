import { useState } from "react";
import { download, getToken, qs, Snapshot } from "../api";
import { Card, ErrorNote, PageHeader } from "../components/ui";
import { fmtTime, useApi } from "../hooks";

const REPORTS = [
  { id: "changes", title: "Change report", text: "Every added, removed and modified row between two snapshots, by device and table.", window: true },
  { id: "compliance", title: "Compliance report", text: "Intent check results: rule status, every violation, and a per-device score.", window: false },
  { id: "inventory", title: "Inventory report", text: "Devices, models, OS versions, serial numbers and hardware modules.", window: false },
];

export default function Reports() {
  const snaps = useApi<{ items: Snapshot[] }>("/snapshots", { limit: 100 });
  const usable = (snaps.data?.items || []).filter((s) => ["complete", "partial"].includes(s.status));
  const [from, setFrom] = useState("previous");
  const [err, setErr] = useState<string | null>(null);
  const [to, setTo] = useState("latest");

  function open(id: string, win: boolean) {
    // HTML reports open in a new tab (print to PDF from there). Fetch with the token, then show the blob.
    const params = win ? { from, to, format: "html" } : { snapshot: to, format: "html" };
    setErr(null);
    const w = window.open("", "_blank");
    fetch(`/api/v1/reports/${id}${qs(params)}`, { headers: { Authorization: `Bearer ${getToken()}` } })
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail || r.statusText);
        return r.blob();
      })
      .then((b) => { if (w) w.location.href = URL.createObjectURL(b); })
      .catch((e) => { w?.close(); setErr(`Report failed: ${e.message}`); });
  }

  const snapOptions = (cur: string, extra: [string, string][]) => (
    <>
      {extra.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      {usable.map((s) => <option key={s.id} value={s.id}>{fmtTime(s.created_at)} {s.labels.join(" ")}</option>)}
      {!extra.some(([v]) => v === cur) && !usable.some((s) => s.id === cur) && <option value={cur}>{cur}</option>}
    </>
  );

  return (
    <>
      <PageHeader title="Reports" sub="Shareable reports in HTML (print to PDF), Excel or CSV" />
      <Card>
        <div className="filters wrap" style={{ marginBottom: 0 }}>
          <label className="inline">From (change report)
            <select value={from} onChange={(e) => setFrom(e.target.value)}>
              {snapOptions(from, [["previous", "Previous snapshot"], ["24h", "24 hours ago"], ["7d", "7 days ago"], ["30d", "30 days ago"]])}
            </select></label>
          <label className="inline">Snapshot
            <select value={to} onChange={(e) => setTo(e.target.value)}>{snapOptions(to, [["latest", "Latest snapshot"]])}</select></label>
        </div>
      </Card>
      <ErrorNote error={err} />
      <div className="report-grid">
        {REPORTS.map((r) => {
          const params = r.window ? { from, to } : { snapshot: to };
          return (
            <Card key={r.id} title={r.title}>
              <p className="muted">{r.text}</p>
              <div className="row gap wrap">
                <button className="btn primary" onClick={() => open(r.id, r.window)}>Open</button>
                <button className="btn" onClick={() => download(`/reports/${r.id}`, { ...params, format: "xlsx" })}>Excel</button>
                <button className="btn" onClick={() => download(`/reports/${r.id}`, { ...params, format: "csv" })}>CSV</button>
              </div>
            </Card>
          );
        })}
      </div>
      <p className="muted">The same reports are available from the API (<code>/api/v1/reports/…</code>) and the CLI (<code>assurance report changes -o changes.xlsx</code>), for scheduled delivery.</p>
    </>
  );
}
