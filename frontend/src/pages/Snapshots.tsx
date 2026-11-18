import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Snapshot } from "../api";
import { useAuth } from "../auth";
import { Badge, Card, Empty, ErrorNote, Modal, PageHeader } from "../components/ui";
import { fmtTime, useApi } from "../hooks";

const RUNNING = ["queued", "running", "finalizing"];

export default function Snapshots() {
  const { can } = useAuth();
  const [label, setLabel] = useState("");
  const { data, error } = useApi<{ items: Snapshot[]; total: number }>("/snapshots", { label, limit: 200 },
    (d) => d.items.some((s) => RUNNING.includes(s.status)));
  const [starting, setStarting] = useState(false);
  const [sel, setSel] = useState<string[]>([]);
  const nav = useNavigate();

  const toggle = (id: string) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s.slice(-1), id]));
  const compare = () => {
    const items = data!.items.filter((s) => sel.includes(s.id)).sort((a, b) => a.created_at.localeCompare(b.created_at));
    nav(`/compare?from=${items[0].id}&to=${items[1].id}`);
  };

  return (
    <>
      <PageHeader title="Snapshots" sub="Every snapshot is immutable and can be compared with any other"
        actions={<>
          <button className="btn" disabled={sel.length !== 2} onClick={compare}>Compare selected</button>
          {can("operator") && <button className="btn primary" onClick={() => setStarting(true)}>New snapshot</button>}
        </>} />
      <ErrorNote error={error} />
      <Card>
        <div className="filters"><input placeholder="Filter by label, e.g. change:CHG0012345" value={label} onChange={(e) => setLabel(e.target.value)} /></div>
        {data && data.items.length === 0 ? <Empty>No snapshots yet.</Empty> : (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th></th><th>Taken</th><th>Status</th><th>Devices</th><th>Changes</th><th>Trigger</th><th>Labels</th><th>Id</th></tr></thead>
              <tbody>
                {data?.items.map((s) => {
                  const ch = s.summary?.changes;
                  return (
                    <tr key={s.id} className={sel.includes(s.id) ? "sel" : ""}>
                      <td><input type="checkbox" aria-label="select" checked={sel.includes(s.id)} onChange={() => toggle(s.id)} disabled={RUNNING.includes(s.status) || s.status === "failed"} /></td>
                      <td><Link to={`/snapshots/${s.id}`}>{fmtTime(s.created_at)}</Link></td>
                      <td><Badge status={s.status} /></td>
                      <td>{s.summary?.devices_ok ?? "…"} / {s.device_count}{s.summary?.devices_failed ? <span className="bad"> · {s.summary.devices_failed} failed</span> : null}</td>
                      <td>{ch ? <Link to={`/compare?from=${ch.previous}&to=${s.id}`}>{ch.total}</Link> : <span className="muted">—</span>}</td>
                      <td>{s.trigger}{s.requested_by ? <span className="muted"> · {s.requested_by}</span> : null}</td>
                      <td>{s.labels.map((l) => <span className="tag" key={l}>{l}</span>)}</td>
                      <td className="mono muted">{s.id.slice(0, 8)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {starting && <NewSnapshot onClose={() => setStarting(false)} onStarted={(id) => nav(`/snapshots/${id}`)} />}
    </>
  );
}

function NewSnapshot({ onClose, onStarted }: { onClose: () => void; onStarted: (id: string) => void }) {
  const sites = useApi<any[]>("/sites");
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
    if (f.mock_scenario) body.mock_scenario = f.mock_scenario;
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
          <details className="span-2"><summary className="muted">Lab options</summary>
            <label>Mock scenario (server must run the mock collector)<input value={f.mock_scenario} onChange={set("mock_scenario")} placeholder="t1" /></label>
          </details>
        </div>
        <ErrorNote error={err} />
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary">Start snapshot</button></div>
      </form>
    </Modal>
  );
}
