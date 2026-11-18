import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Change, Device, TABLES } from "../api";
import { useAuth } from "../auth";
import { Badge, Card, Empty, ErrorNote, PageHeader } from "../components/ui";
import { fmtTime, useApi } from "../hooks";
import { ViolationTable } from "./Checks";
import { ChangeList } from "./Compare";
import { DeviceForm } from "./Devices";
import { StateTable } from "./SnapshotDetail";

export default function DevicePage() {
  const { name = "" } = useParams();
  const { can } = useAuth();
  const nav = useNavigate();
  const dev = useApi<Device>(`/devices/${name}`);
  const [since, setSince] = useState("7d");
  const hist = useApi<any>(`/devices/${name}/history`, { since });
  const [tab, setTab] = useState<string>("interfaces");
  const [editing, setEditing] = useState(false);
  const checks = useApi<any>("/snapshots/latest/checks", { device: name });
  const config = useApi<string>(tab === "config" ? `/devices/${name}/config` : null);
  const d = dev.data;

  async function remove() {
    if (!confirm(`Remove ${name} from inventory? Its history in past snapshots is kept.`)) return;
    await api(`/devices/${name}`, { method: "DELETE" });
    nav("/devices");
  }

  async function resetKey() {
    if (!confirm(`Forget the pinned SSH host key for ${name}? The next collection pins whatever key the device presents, so check the new fingerprint afterwards.`)) return;
    await api(`/devices/${name}/host-key`, { method: "DELETE" });
    dev.reload();
  }

  if (dev.error) return <ErrorNote error={dev.error} />;
  if (!d) return <div className="muted">Loading…</div>;
  return (
    <>
      <PageHeader title={d.name} sub={<><Link to="/devices">Devices</Link> · {d.platform} · {d.host}</>}
        actions={can("admin") && <><button className="btn" onClick={() => setEditing(true)}>Edit</button><button className="btn danger" onClick={remove}>Remove</button></>} />
      <div className="grid-2">
        <Card title="Facts">
          <dl className="facts">
            <dt>Status</dt><dd><Badge status={d.status} /></dd>
            <dt>Site / role</dt><dd>{d.site || "—"} / {d.role || "—"}</dd>
            <dt>Model</dt><dd>{d.model || "—"}</dd>
            <dt>Serial</dt><dd className="mono">{d.serial || "—"}</dd>
            <dt>OS version</dt><dd className="mono">{d.os_version || "—"}</dd>
            <dt>Credential</dt><dd>{d.credential || "inherited"}</dd>
            <dt>Last collection</dt><dd>{d.last_collection_status ? <><Badge status={d.last_collection_status} /> {fmtTime(d.last_collected_at)}</> : "never"}</dd>
            <dt>Tags</dt><dd>{d.tags.map((t) => <span className="tag" key={t}>{t}</span>)}</dd>
            <dt>SSH host key</dt><dd>{d.ssh_host_key_fingerprint
              ? <><span className="mono">{d.ssh_host_key_type} {d.ssh_host_key_fingerprint}</span>
                <div className="muted">pinned {fmtTime(d.ssh_host_key_pinned_at)}
                  {can("admin") && <> · <button className="btn sm ghost" onClick={resetKey}>Reset pin</button></>}</div></>
              : <span className="muted">not pinned yet (pinned on first collection)</span>}</dd>
          </dl>
        </Card>
        <Card title="Change history" actions={
          <select value={since} onChange={(e) => setSince(e.target.value)}>
            <option value="24h">24 hours</option><option value="7d">7 days</option><option value="30d">30 days</option><option value="all">All</option>
          </select>}>
          {hist.data?.events?.length ? (
            <ol className="timeline">
              {hist.data.events.map((ev: any) => (
                <li key={ev.to.id}>
                  <Link to={`/compare?from=${ev.from.id}&to=${ev.to.id}&device=${name}`}>{fmtTime(ev.to.created_at)}</Link>
                  <span className="muted"> · {Object.entries(ev.summary).map(([t, c]: any) => `${t} ${c.added + c.removed + c.changed}`).join(", ")}</span>
                </li>
              ))}
            </ol>
          ) : <Empty>No changes in this window{hist.data ? ` (${hist.data.snapshots} snapshot(s))` : ""}.</Empty>}
        </Card>
      </div>
      {hist.data?.events?.[0] && (
        <Card title={`Latest change · ${fmtTime(hist.data.events[0].to.created_at)}`}>
          <ChangeList changes={hist.data.events[0].changes as Change[]} hideDevice />
        </Card>
      )}
      <Card title="Intent checks" actions={<Link to="/checks">All rules</Link>}>
        <ViolationTable rows={checks.data?.violations || []} hideDevice />
      </Card>
      <Card title="Current state" actions={<div className="tabs">{TABLES.map((t) => <button key={t} className={t === tab ? "tab active" : "tab"} onClick={() => setTab(t)}>{t}</button>)}</div>}>
        {tab === "config" ? (
          <>
            <div className="filters">
              <span className="muted">Running configuration, secrets masked. Diffs per section are in Compare.</span>
              <Link className="btn sm" to={`/compare?device=${name}&table=config`}>Config changes</Link>
            </div>
            {config.error ? <Empty>{config.error}</Empty> : <pre className="cfg cfg-full">{config.data || "Loading…"}</pre>}
          </>
        ) : <StateTable snapshot="latest" table={tab} device={name} />}
      </Card>
      {editing && <DeviceForm device={d} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); dev.reload(); }} />}
    </>
  );
}
