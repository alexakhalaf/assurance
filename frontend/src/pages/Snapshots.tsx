import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Snapshot, toast } from "../api";
import { useAuth } from "../auth";
import { Col, DataGrid } from "../components/DataGrid";
import { Icon } from "../components/Icon";
import { Badge, ErrorNote, Modal, PageHeader } from "../components/ui";
import { duration, fmtTime, useApi } from "../hooks";
import { DIFFABLE, useSnapshot } from "../snapshot";

const RUNNING = ["queued", "running", "finalizing"];

export default function Snapshots() {
  const { can } = useAuth();
  const { data, error, loading, reload } = useApi<{ items: Snapshot[]; total: number }>("/snapshots", { limit: 500 },
    (d) => d.items.some((s) => RUNNING.includes(s.status)));
  const [starting, setStarting] = useState(false);
  const [sel, setSel] = useState<string[]>([]);
  const nav = useNavigate();
  const snapCtx = useSnapshot();
  const items = data?.items || [];

  const toggle = (id: string) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s.slice(-1), id]));
  const compare = () => {
    const chosen = items.filter((s) => sel.includes(s.id)).sort((a, b) => a.created_at.localeCompare(b.created_at));
    nav(`/compare?from=${chosen[0].id}&to=${chosen[1].id}`);
  };
  async function remove(s: Snapshot) {
    if (!confirm(`Delete the snapshot from ${fmtTime(s.created_at)}? Its state tables and check results are removed for good.`)) return;
    try {
      await api(`/snapshots/${s.id}`, { method: "DELETE" });
      toast("Snapshot deleted");
      setSel((x) => x.filter((i) => i !== s.id));
      reload();
      snapCtx.reload();
    } catch (e: any) { toast(e.message, "bad"); }
  }
  const columns: Col<Snapshot>[] = [
    { key: "sel", label: "", filter: false, sortable: false, width: 34, render: (s) => (
      <input type="checkbox" aria-label="select for compare" checked={sel.includes(s.id)} onClick={(e) => e.stopPropagation()} onChange={() => toggle(s.id)}
        disabled={RUNNING.includes(s.status) || s.status === "failed"} />) },
    { key: "created_at", label: "Taken", render: (s) => <Link to={`/snapshots/${s.id}`} onClick={(e) => e.stopPropagation()}>{fmtTime(s.created_at)}</Link>, text: (s) => fmtTime(s.created_at), sortValue: (s) => s.created_at },
    { key: "status", label: "Status", filter: "select", options: ["complete", "partial", "failed", "running", "queued", "finalizing"], render: (s) => <Badge status={s.status} /> },
    { key: "devices", label: "Devices", num: true, filter: false, text: (s) => String(s.device_count), sortValue: (s) => s.device_count,
      render: (s) => <>{s.summary?.devices_ok ?? "…"} / {s.device_count}{s.summary?.devices_failed ? <span className="bad"> · {s.summary.devices_failed} failed</span> : null}</> },
    { key: "changes", label: "Changes", num: true, filter: false, text: (s) => String(s.summary?.changes?.total ?? ""), sortValue: (s) => s.summary?.changes?.total ?? -1,
      render: (s) => s.summary?.changes ? <Link to={`/compare?from=${s.summary.changes.previous}&to=${s.id}`} onClick={(e) => e.stopPropagation()}>{s.summary.changes.total}</Link> : <span className="faint">—</span> },
    { key: "checks", label: "Checks", filter: false, sortable: false, text: () => "",
      render: (s) => s.summary?.checks ? <span className="row gap">{s.summary.checks.fail > 0 && <span className="badge badge-fail">{s.summary.checks.fail}</span>}
        {s.summary.checks.warning > 0 && <span className="badge badge-warning">{s.summary.checks.warning}</span>}
        {!s.summary.checks.fail && !s.summary.checks.warning && <span className="badge badge-pass">pass</span>}</span> : <span className="faint">—</span> },
    { key: "duration", label: "Duration", num: true, filter: false, text: () => "",
      render: (s) => s.finished_at && s.started_at ? duration(+new Date(s.finished_at) - +new Date(s.started_at)) : <span className="faint">…</span> },
    { key: "trigger", label: "Trigger", filter: "select", options: ["api", "cli", "schedule", "ui", "discovery"], render: (s) => <>{s.trigger}{s.requested_by ? <span className="muted"> · {s.requested_by}</span> : null}</> },
    { key: "labels", label: "Labels", text: (s) => s.labels.join(" "), sortable: false, render: (s) => s.labels.map((l) => <span className="tag" key={l}>{l}</span>) },
    { key: "id", label: "Id", mono: true, hidden: true },
    { key: "actions", label: "", filter: false, sortable: false, render: (s) => (
      <div className="row gap end" onClick={(e) => e.stopPropagation()}>
        {DIFFABLE.includes(s.status) && <button className="btn sm ghost" onClick={() => { snapCtx.setRef(s.id); toast(`Showing data from ${fmtTime(s.created_at)}`); }}>View</button>}
        {can("admin") && !RUNNING.includes(s.status) && <button className="btn sm ghost danger" onClick={() => remove(s)} aria-label="Delete snapshot"><Icon name="trash" size={13} /></button>}
      </div>) },
  ];
  return (
    <div className="page">
      <PageHeader title="Snapshots" sub="Every snapshot is immutable and can be compared with any other. Labelled snapshots are kept by retention."
        actions={<>
          <button className="btn" disabled={sel.length !== 2} onClick={compare}><Icon name="compare" size={14} /> Compare selected{sel.length ? ` (${sel.length}/2)` : ""}</button>
          {can("operator") && <button className="btn primary" onClick={() => setStarting(true)}><Icon name="play" size={14} /> New snapshot</button>}
        </>} />
      <ErrorNote error={error} />
      <section className="card">
        <DataGrid storageKey="snapshots" columns={columns} rows={items} loading={loading} rowKey={(s) => s.id}
          onRowClick={(s) => nav(`/snapshots/${s.id}`)} emptyText="No snapshots yet." />
      </section>
      {starting && <NewSnapshot onClose={() => setStarting(false)} onStarted={(id) => { snapCtx.reload(); nav(`/snapshots/${id}`); }} />}
    </div>
  );
}

function NewSnapshot({ onClose, onStarted }: { onClose: () => void; onStarted: (id: string) => void }) {
  const sites = useApi<any[]>("/sites");
  const info = useApi<{ collector: string }>("/system/info");
  const lab = info.data?.collector === "mock";
  const [f, setF] = useState({ site: "", role: "", platform: "", tags: "", devices: "", labels: "", mock_scenario: "" });
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const scope: any = {};
    if (f.site) scope.site = f.site;
    if (f.role) scope.role = f.role;
    if (f.platform) scope.platform = f.platform;
    if (f.tags) scope.tags = list(f.tags);
    if (f.devices) scope.devices = list(f.devices);
    const body: any = { scope, labels: list(f.labels) };
    if (lab && f.mock_scenario) body.mock_scenario = f.mock_scenario;
    try {
      const s = await api<Snapshot>("/snapshots", { method: "POST", body: JSON.stringify(body) });
      onStarted(s.id);
    } catch (e: any) {
      setErr(e.message);
    }
  }

  return (
    <Modal title="New snapshot" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <p className="muted">Leave the scope empty to snapshot every active device. For a change window, add a label such as <code>change:CHG0012345</code> to the pre and post snapshots.</p>
        <div className="form-grid">
          <label>Site<select value={f.site} onChange={set("site")}><option value="">Any</option>{sites.data?.map((s) => <option key={s.name}>{s.name}</option>)}</select></label>
          <label>Platform<select value={f.platform} onChange={set("platform")}><option value="">Any</option><option value="iosxe">IOS-XE</option><option value="nxos">NX-OS</option></select></label>
          <label>Role<input value={f.role} onChange={set("role")} /></label>
          <label>Tags (all must match)<input value={f.tags} onChange={set("tags")} placeholder="core, dc" /></label>
          <label className="span-2">Devices<input value={f.devices} onChange={set("devices")} placeholder="Comma separated names" /></label>
          <label className="span-2">Labels<input value={f.labels} onChange={set("labels")} placeholder="change:CHG0012345, pre" /></label>
          {lab && (
            <details className="span-2"><summary className="muted">Lab options</summary>
              <label>Mock scenario (lab data set to read, e.g. t1 or t2)<input value={f.mock_scenario} onChange={set("mock_scenario")} placeholder="t1" /></label>
            </details>
          )}
        </div>
        <ErrorNote error={err} />
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary">Start snapshot</button></div>
      </form>
    </Modal>
  );
}
