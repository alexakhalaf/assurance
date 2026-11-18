import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Device, download } from "../api";
import { useAuth } from "../auth";
import { Col, DataGrid } from "../components/DataGrid";
import { Icon } from "../components/Icon";
import { Badge, ErrorNote, Modal, PageHeader, Stat } from "../components/ui";
import { ago, useApi } from "../hooks";

export default function Devices() {
  const { can } = useAuth();
  const nav = useNavigate();
  const { data, error, loading, reload } = useApi<{ items: Device[]; total: number }>("/devices", { limit: 1000 });
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const items = data?.items || [];
  const opts = (f: (d: Device) => string | null) => [...new Set(items.map(f).filter(Boolean) as string[])].sort();
  const failed = items.filter((d) => d.last_collection_status === "failed").length;
  const columns: Col<Device>[] = [
    { key: "name", label: "Device", render: (d) => <Link to={`/devices/${encodeURIComponent(d.name)}`} onClick={(e) => e.stopPropagation()}><strong>{d.name}</strong></Link>, text: (d) => d.name },
    { key: "host", label: "Management IP", mono: true },
    { key: "platform", label: "Platform", filter: "select", options: ["iosxe", "nxos"] },
    { key: "site", label: "Site", filter: "select", options: opts((d) => d.site) },
    { key: "role", label: "Role", filter: "select", options: opts((d) => d.role) },
    { key: "model", label: "Model" },
    { key: "os_version", label: "Version", mono: true },
    { key: "serial", label: "Serial", mono: true, hidden: true },
    { key: "hostname", label: "Hostname", hidden: true },
    { key: "tags", label: "Tags", sortable: false, render: (d) => d.tags.map((t) => <span key={t} className="tag">{t}</span>), text: (d) => d.tags.join(" ") },
    { key: "status", label: "Status", filter: "select", options: ["active", "maintenance", "retired"], render: (d) => <Badge status={d.status} /> },
    { key: "last_collection_status", label: "Last collection", filter: "select", options: ["ok", "partial", "failed"],
      render: (d) => d.last_collection_status ? <span className="row gap"><Badge status={d.last_collection_status} /><span className="muted small">{ago(d.last_collected_at)}</span></span> : <span className="faint">never</span>,
      text: (d) => d.last_collection_status || "" },
    { key: "credential", label: "Credential", hidden: true, text: (d) => d.credential || "inherited" },
    { key: "ssh_host_key_fingerprint", label: "SSH host key", mono: true, hidden: true },
  ];
  return (
    <div className="page">
      <PageHeader title="Devices" crumbs={[["Inventory"]]} sub="Every device Assurance collects. Inventory is owned here; no external source of truth."
        actions={<>
          <button className="btn" onClick={() => download("/devices/export.csv")}><Icon name="download" size={14} /> Export</button>
          {can("admin") && <button className="btn" onClick={() => setImporting(true)}><Icon name="upload" size={14} /> Import CSV</button>}
          {can("admin") && <button className="btn primary" onClick={() => setAdding(true)}><Icon name="plus" size={14} /> Add device</button>}
        </>} />
      <div className="stats">
        <Stat label="Devices" value={items.length} hint={`${items.filter((d) => d.status === "active").length} active`} />
        <Stat label="IOS-XE / NX-OS" value={`${items.filter((d) => d.platform === "iosxe").length} / ${items.filter((d) => d.platform === "nxos").length}`} hint="platforms" />
        <Stat label="Sites" value={opts((d) => d.site).length} hint={`${items.filter((d) => !d.site).length} unassigned`} />
        <Stat label="Last collection failed" value={failed} tone={failed ? "bad" : "good"} hint={failed ? "check credentials and reachability" : "all collected"} />
      </div>
      <section className="card">
        <DataGrid storageKey="devices" columns={columns} rows={items} loading={loading} error={error} rowKey={(d) => String(d.id)}
          onRowClick={(d) => nav(`/devices/${encodeURIComponent(d.name)}`)} searchPlaceholder="Search name, IP, model, serial"
          emptyText={items.length ? "No devices match." : "No devices yet. Add one or import a CSV."} />
      </section>
      {adding && <DeviceForm onClose={() => setAdding(false)} onSaved={() => { setAdding(false); reload(); }} />}
      {importing && <ImportCsv onClose={() => setImporting(false)} onDone={() => { setImporting(false); reload(); }} />}
    </div>
  );
}

export function DeviceForm({ device, onClose, onSaved }: { device?: Device; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({
    name: device?.name || "", host: device?.host || "", platform: device?.platform || "iosxe", port: device?.port || 22,
    site: device?.site || "", role: device?.role || "", tags: device?.tags.join(", ") || "", credential: device?.credential || "",
    status: device?.status || "active",
  });
  const [err, setErr] = useState<string | null>(null);
  const creds = useApi<any[]>("/credentials");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    const body = { ...f, port: Number(f.port), site: f.site || null, role: f.role || null, credential: f.credential || null,
      tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean) };
    try {
      if (device) {
        const { name: _n, ...patch } = body;
        await api(`/devices/${device.name}`, { method: "PATCH", body: JSON.stringify(patch) });
      } else await api("/devices", { method: "POST", body: JSON.stringify(body) });
      onSaved();
    } catch (e: any) {
      setErr(e.message);
    }
  }

  return (
    <Modal title={device ? `Edit ${device.name}` : "Add device"} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <div className="form-grid">
          <label>Name<input value={f.name} onChange={set("name")} disabled={!!device} required /></label>
          <label>Management IP / host<input value={f.host} onChange={set("host")} required /></label>
          <label>Platform<select value={f.platform} onChange={set("platform")}><option value="iosxe">IOS-XE</option><option value="nxos">NX-OS</option></select></label>
          <label>SSH port<input type="number" value={f.port} onChange={set("port")} /></label>
          <label>Site<input value={f.site} onChange={set("site")} placeholder="Created if new" /></label>
          <label>Role<input value={f.role} onChange={set("role")} placeholder="core-router" /></label>
          <label>Tags<input value={f.tags} onChange={set("tags")} placeholder="core, dc" /></label>
          <label>Credential profile<select value={f.credential} onChange={set("credential")}>
            <option value="">Inherit (site, then "default")</option>
            {creds.data?.map((c) => <option key={c.name}>{c.name}</option>)}
          </select></label>
          {device && <label>Status<select value={f.status} onChange={set("status")}><option>active</option><option>maintenance</option><option>retired</option></select></label>}
        </div>
        <ErrorNote error={err} />
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary">Save</button></div>
      </form>
    </Modal>
  );
}

function ImportCsv({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  async function send(dry: boolean) {
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await api("/devices/import", { method: "POST", body: fd, params: { dry_run: dry } });
      if (dry) setPreview(r);
      else onDone();
    } catch (e: any) {
      setErr(e.message);
    }
  }

  return (
    <Modal title="Import devices from CSV" onClose={onClose}>
      <p className="muted">Columns: <code>name,host,platform</code> required; <code>site,role,tags,port,status,credential</code> optional. Tags are separated by <code>;</code>. Devices are matched by name.</p>
      <input type="file" accept=".csv,text/csv" onChange={(e) => { setFile(e.target.files?.[0] || null); setPreview(null); }} />
      <ErrorNote error={err} />
      {preview && (
        <div className="preview">
          <div className="row gap wrap">
            <span className="chg chg-added">{preview.created.length} new</span>
            <span className="chg chg-changed">{preview.updated.length} updated</span>
            <span className="muted">{preview.unchanged.length} unchanged</span>
            {preview.sites_created.length > 0 && <span className="muted">{preview.sites_created.length} new site(s)</span>}
          </div>
          {preview.errors.length > 0 && (
            <table className="tbl"><thead><tr><th>Line</th><th>Name</th><th>Problem</th></tr></thead>
              <tbody>{preview.errors.map((e: any) => <tr key={e.line}><td>{e.line}</td><td>{e.name}</td><td className="bad">{e.error}</td></tr>)}</tbody></table>
          )}
        </div>
      )}
      <div className="row gap end">
        <button className="btn" onClick={() => send(true)} disabled={!file}>Preview</button>
        <button className="btn primary" onClick={() => send(false)} disabled={!preview || preview.created.length + preview.updated.length === 0}>Apply import</button>
      </div>
    </Modal>
  );
}
