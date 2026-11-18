import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { api, Device, download } from "../api";
import { useAuth } from "../auth";
import { Badge, Card, Empty, ErrorNote, Modal, PageHeader } from "../components/ui";
import { ago, useApi } from "../hooks";

export default function Devices() {
  const { can } = useAuth();
  const [q, setQ] = useState("");
  const [platform, setPlatform] = useState("");
  const [status, setStatus] = useState("");
  const { data, error, reload } = useApi<{ items: Device[]; total: number }>("/devices", { q, platform, status, limit: 1000 });
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);

  return (
    <>
      <PageHeader title="Devices" sub={data ? `${data.total} device(s) in inventory` : undefined}
        actions={<>
          <button className="btn" onClick={() => download("/devices/export.csv")}>Export CSV</button>
          {can("admin") && <button className="btn" onClick={() => setImporting(true)}>Import CSV</button>}
          {can("admin") && <button className="btn primary" onClick={() => setAdding(true)}>Add device</button>}
        </>} />
      <ErrorNote error={error} />
      <Card>
        <div className="filters">
          <input placeholder="Search name, IP, model, serial" value={q} onChange={(e) => setQ(e.target.value)} />
          <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
            <option value="">All platforms</option><option value="iosxe">IOS-XE</option><option value="nxos">NX-OS</option>
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any status</option><option>active</option><option>maintenance</option><option>retired</option>
          </select>
        </div>
        {data && data.items.length === 0 ? <Empty>No devices match.</Empty> : (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th>Name</th><th>Host</th><th>Platform</th><th>Site</th><th>Role</th><th>Model</th><th>Version</th><th>Status</th><th>Last collection</th></tr></thead>
              <tbody>
                {data?.items.map((d) => (
                  <tr key={d.id}>
                    <td><Link to={`/devices/${d.name}`}><strong>{d.name}</strong></Link>
                      {d.tags.length > 0 && <div className="tags">{d.tags.map((t) => <span key={t} className="tag">{t}</span>)}</div>}</td>
                    <td className="mono">{d.host}</td><td>{d.platform}</td><td>{d.site || "—"}</td><td>{d.role || "—"}</td>
                    <td>{d.model || "—"}</td><td className="mono">{d.os_version || "—"}</td>
                    <td><Badge status={d.status} /></td>
                    <td>{d.last_collection_status ? <><Badge status={d.last_collection_status} /> <span className="muted">{ago(d.last_collected_at)}</span></> : <span className="muted">never</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {adding && <DeviceForm onClose={() => setAdding(false)} onSaved={() => { setAdding(false); reload(); }} />}
      {importing && <ImportCsv onClose={() => setImporting(false)} onDone={() => { setImporting(false); reload(); }} />}
    </>
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
