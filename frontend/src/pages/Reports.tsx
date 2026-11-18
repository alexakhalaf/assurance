import { useState } from "react";
import { api, download, getToken, qs, Snapshot } from "../api";
import { useAuth } from "../auth";
import { Card, Empty, ErrorNote, PageHeader } from "../components/ui";
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
    <div className="page">
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
      <ScheduledReports />
      <p className="muted">The same reports are available from the API (<code>/api/v1/reports/…</code>) and the CLI (<code>assurance report changes -o changes.xlsx</code>).</p>
    </div>
  );
}

function ScheduledReports() {
  const { can } = useAuth();
  const list = useApi<any[]>("/report-schedules");
  const [f, setF] = useState({ name: "", report: "changes", format: "xlsx", window: "24h", cron: "0 7 * * 1-5", recipients: "" });
  const [err, setErr] = useState<string | null>(null);
  const upd = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }));

  async function create() {
    setErr(null);
    try {
      await api("/report-schedules", { method: "POST", body: JSON.stringify({ ...f, recipients: f.recipients.split(/[\s,;]+/).filter(Boolean) }) });
      setF((x) => ({ ...x, name: "", recipients: "" })); list.reload();
    } catch (e: any) { setErr(e.message); }
  }
  async function run(name: string) { await api(`/report-schedules/${name}/run`, { method: "POST" }); list.reload(); }
  async function toggle(s: any) { await api(`/report-schedules/${s.name}`, { method: "PATCH", body: JSON.stringify({ enabled: !s.enabled }) }); list.reload(); }
  async function remove(name: string) { if (confirm(`Delete scheduled report ${name}?`)) { await api(`/report-schedules/${name}`, { method: "DELETE" }); list.reload(); } }

  return (
    <Card title="Scheduled reports" actions={<span className="muted">Emailed as attachments. Times are UTC cron.</span>}>
      {(list.data || []).length === 0 ? <Empty>No scheduled reports.</Empty> : (
        <table className="tbl dense">
          <thead><tr><th>Name</th><th>Report</th><th>Schedule</th><th>Recipients</th><th>Last run</th><th></th></tr></thead>
          <tbody>{list.data!.map((s) => (
            <tr key={s.name}>
              <td><strong>{s.name}</strong>{!s.enabled && <span className="tag">paused</span>}</td>
              <td>{s.report} · {s.format}{s.report === "changes" ? ` · last ${s.window}` : ""}</td>
              <td className="mono">{s.cron}<div className="muted">next {fmtTime(s.next_run_at)}</div></td>
              <td>{s.recipients.join(", ")}</td>
              <td>{s.last_run_at ? <>{fmtTime(s.last_run_at)}<div className={s.last_status?.startsWith("failed") ? "bad" : "muted"}>{s.last_status}</div></> : "never"}</td>
              <td>{can("admin") && <div className="row gap end">
                <button className="btn sm" onClick={() => run(s.name)}>Send now</button>
                <button className="btn sm ghost" onClick={() => toggle(s)}>{s.enabled ? "Pause" : "Resume"}</button>
                <button className="btn sm ghost danger" onClick={() => remove(s.name)}>Delete</button></div>}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
      {can("admin") && (
        <div className="form-inline">
          <input placeholder="name" value={f.name} onChange={(e) => upd("name", e.target.value)} style={{ width: 140 }} />
          <select value={f.report} onChange={(e) => upd("report", e.target.value)}><option value="changes">Changes</option><option value="compliance">Compliance</option><option value="inventory">Inventory</option></select>
          <select value={f.format} onChange={(e) => upd("format", e.target.value)}><option value="xlsx">Excel</option><option value="html">HTML</option><option value="csv">CSV</option></select>
          {f.report === "changes" && <select value={f.window} onChange={(e) => upd("window", e.target.value)}><option value="24h">last 24 h</option><option value="7d">last 7 days</option><option value="30d">last 30 days</option></select>}
          <input className="mono" placeholder="cron" value={f.cron} onChange={(e) => upd("cron", e.target.value)} style={{ width: 130 }} />
          <input placeholder="recipients, comma separated" value={f.recipients} onChange={(e) => upd("recipients", e.target.value)} style={{ minWidth: 260 }} />
          <button className="btn primary" disabled={!f.name || !f.recipients} onClick={create}>Schedule</button>
        </div>
      )}
      <ErrorNote error={err} />
    </Card>
  );
}
